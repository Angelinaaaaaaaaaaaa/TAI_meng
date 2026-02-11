#!/usr/bin/env python3
"""
bfs_v1.py

Core Traversal and Classification Mechanism for the File Reorganization Agent.

This module implements a breadth-first search (BFS) traversal over the file system,
starting from the course root, to make top-down classification decisions before
inspecting finer-grained details.

Design Philosophy:
  - Top-level folders often encode the strongest semantic signals (e.g., "hw", "disc",
    "slides", "resources"), and classifying them early provides a stable global
    structure for the rest of the system.
  - When the classifier is confident at the folder level, the folder is treated as a
    unit and all its contents inherit the classification.
  - When confidence is low or the folder appears semantically mixed, the traversal
    descends to classify individual files within it.

Categories:
  - Study: Lectures, slides, readings, videos (instructional content)
  - Practice: Homework, labs, projects (student-produced work)
  - Support: Syllabus, calendars, logistics, tools
  - Skip: Generated files, build artifacts, or content that should not be reorganized

Pipeline:
  1. Build the folder tree from the file system (reuses file_organizer_v6 infrastructure)
  2. BFS traversal starting from root
  3. For each folder/file encountered:
     - Call classifier to get category + confidence
     - If folder is confident -> assign category to all contents
     - If folder is low confidence or mixed -> enqueue children for individual processing
  4. Return all recorded classifications

Usage:
  from bfs_v1 import BFSTraverser, TraversalResult

  traverser = BFSTraverser(classifier=my_classifier)
  result = traverser.traverse(source_path="/path/to/course", db_path="metadata.db")

  for path, classification in result.classifications.items():
      print(f"{path}: {classification.category} (confidence: {classification.confidence})")
"""

import os
import sqlite3
from typing import List, Optional, Dict, Set, Callable, Protocol, Union
from dataclasses import dataclass, field
from collections import deque
from enum import Enum


# ============================ Constants ============================

DEFAULT_EXCLUDE_DIRS = {
    ".git", "__pycache__", "node_modules",
    ".DS_Store", ".ipynb_checkpoints",
    "venv", ".venv", "env", ".env",
}

# Confidence threshold for folder-level classification
# Below this threshold, we descend into children
DEFAULT_CONFIDENCE_THRESHOLD = 0.7

# Categories for classification
class Category(str, Enum):
    STUDY = "study"
    PRACTICE = "practice"
    SUPPORT = "support"
    SKIP = "skip"


# ============================ Data Structures ============================

@dataclass
class FileMeta:
    """Metadata for a single file."""
    source_path: str        # relative path from root, e.g. "disc/disc01/x.html"
    folder_path: str        # parent folder relative path, e.g. "disc/disc01"
    file_name: str
    description: Optional[str] = None


@dataclass
class FolderNode:
    """Tree node representing a folder in the file system."""
    path: str               # relative path from root
    name: str               # folder name (basename)
    files: List[FileMeta] = field(default_factory=list)
    children: Dict[str, "FolderNode"] = field(default_factory=dict)

    def is_leaf(self) -> bool:
        """Check if this folder has no subfolders."""
        return len(self.children) == 0

    def total_file_count(self) -> int:
        """Recursively count all files under this folder."""
        count = len(self.files)
        for child in self.children.values():
            count += child.total_file_count()
        return count


@dataclass
class ClassificationResult:
    """Result of classifying a folder or file."""
    category: Category
    confidence: float           # 0.0 to 1.0
    reason: str                 # Explanation for the classification
    is_mixed: bool = False      # True if folder contains semantically mixed content

    def is_confident(self, threshold: float = DEFAULT_CONFIDENCE_THRESHOLD) -> bool:
        """Check if classification confidence meets the threshold."""
        return self.confidence >= threshold and not self.is_mixed


@dataclass
class Classification:
    """Final classification record for a file or folder."""
    path: str                   # relative path
    category: Category
    confidence: float
    reason: str
    classified_at_level: str    # "folder" or "file" - indicates granularity of decision
    parent_folder: Optional[str] = None  # If classified via folder inheritance


@dataclass
class TraversalResult:
    """Complete result of the BFS traversal."""
    classifications: Dict[str, Classification]  # path -> Classification
    folder_decisions: Dict[str, ClassificationResult]  # folder path -> decision made
    skipped_folders: List[str]
    files_classified_individually: int
    files_classified_via_folder: int


# ============================ Classifier Protocol ============================

class Classifier(Protocol):
    """
    Protocol defining the interface for a classifier.

    Implementations should provide classification logic based on:
    - Aggregated metadata from the database
    - Representative file summaries
    - Learned heuristics
    - Folder/file naming patterns

    This is a placeholder interface
    """

    def classify_folder(
        self,
        node: FolderNode,
        db_context: Optional[Dict[str, str]] = None,
    ) -> ClassificationResult:
        """
        Classify a folder based on its structure and contents.

        Args:
            node: The FolderNode to classify
            db_context: Optional dictionary mapping filenames to descriptions

        Returns:
            ClassificationResult with category, confidence, and reasoning
        """
        ...

    def classify_file(
        self,
        file_meta: FileMeta,
        db_context: Optional[Dict[str, str]] = None,
    ) -> ClassificationResult:
        """
        Classify an individual file.

        Args:
            file_meta: The FileMeta object for the file
            db_context: Optional dictionary mapping filenames to descriptions

        Returns:
            ClassificationResult with category, confidence, and reasoning
        """
        ...


# ============================ Tree Building (from file_organizer_v6) ============================

def scan_directory(
    root_dir: str,
    max_depth: Optional[int] = None,
    exclude_dirs: Optional[Set[str]] = None,
) -> List[str]:
    """
    Scan all files under root_dir and return relative paths.

    Adapted from file_organizer_v6._scan_directory
    """
    out: List[str] = []
    root_dir = os.path.abspath(root_dir)
    exclude_dirs = exclude_dirs or DEFAULT_EXCLUDE_DIRS

    for cur_root, dirnames, filenames in os.walk(root_dir):
        rel_dir = os.path.relpath(cur_root, root_dir)
        rel_dir = "." if rel_dir == "." else rel_dir.replace("\\", "/")

        if max_depth is not None and rel_dir != ".":
            depth = rel_dir.count("/") + 1
            if depth > max_depth:
                dirnames[:] = []
                continue

        dirnames[:] = [
            d for d in dirnames
            if (d not in exclude_dirs and not d.startswith("."))
        ]

        for fn in sorted(filenames):
            if fn.startswith("."):
                continue
            full = os.path.join(cur_root, fn)
            rel = os.path.relpath(full, root_dir).replace("\\", "/")
            out.append(rel)

    return out


def load_descriptions(db_path: Optional[str]) -> Dict[str, str]:
    """
    Load file descriptions from SQLite database.

    Returns a dictionary mapping filename -> description.
    Adapted from file_organizer_v6._load_descriptions_safe
    """
    if not db_path or not os.path.exists(db_path):
        return {}

    mapping: Dict[str, str] = {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        cur = conn.cursor()
        cur.execute("SELECT relative_path, file_name, description FROM file")
        rows = cur.fetchall()

        for r in rows:
            desc = r["description"]
            if not desc:
                continue
            rel = r["relative_path"] or ""
            fname = os.path.basename(rel) if rel else (r["file_name"] or "")
            if fname and fname not in mapping:
                mapping[fname] = desc

    except sqlite3.OperationalError as e:
        print(f"[DB] ERROR querying file table: {e}")
        mapping = {}
    finally:
        conn.close()

    return mapping


def build_tree(
    root_dir: str,
    files_on_disk: List[str],
    path2desc: Dict[str, str],
) -> FolderNode:
    """
    Build a folder tree from scanned files and attach descriptions.

    Adapted from file_organizer_v6._build_tree
    """
    root = FolderNode(path=".", name="root")

    for rel_path in files_on_disk:
        folder = os.path.dirname(rel_path).replace("\\", "/") or "."
        fname = os.path.basename(rel_path)

        node = root
        if folder != ".":
            parts = folder.split("/")
            agg: List[str] = []
            for p in parts:
                agg.append(p)
                ppath = "/".join(agg)
                if p not in node.children:
                    node.children[p] = FolderNode(path=ppath, name=p)
                node = node.children[p]

        desc = path2desc.get(fname)

        node.files.append(
            FileMeta(
                source_path=rel_path,
                folder_path=folder,
                file_name=fname,
                description=desc,
            )
        )

    return root


# ============================ BFS Traverser ============================

class BFSTraverser:
    """
    Core BFS traversal engine for the file reorganization agent.

    This class implements the traverse-to-classify pipeline:
    1. Start from the course root
    2. Process folders/files in BFS order (top-level first)
    3. Make classification decisions using the provided classifier
    4. When confident at folder level -> assign category to all contents
    5. When low confidence or mixed -> descend to classify individual files

    Usage:
        classifier = MyClassifier()  # or PlaceholderClassifier()
        traverser = BFSTraverser(classifier=classifier)
        result = traverser.traverse("/path/to/course", db_path="metadata.db")
    """

    def __init__(
        self,
        classifier: Optional[Classifier] = None,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        exclude_dirs: Optional[Set[str]] = None,
    ):
        self.classifier = classifier()
        self.confidence_threshold = confidence_threshold
        self.exclude_dirs = exclude_dirs or DEFAULT_EXCLUDE_DIRS

    def traverse(
        self,
        source_path: str,
        db_path: Optional[str] = None,
        max_depth: Optional[int] = None,
    ) -> TraversalResult:
        """
        Perform BFS traversal and classification of the course directory.

        This is the main entry point implementing the t2c2spr algorithm:
        Traverse-to-Classify-to-(Study/Practice/Support/Skip)

        Args:
            source_path: Root directory of the course materials
            db_path: Optional path to SQLite database with file descriptions
            max_depth: Optional maximum traversal depth

        Returns:
            TraversalResult containing all classifications and statistics
        """
        source_path = os.path.abspath(source_path)
        if not os.path.isdir(source_path):
            raise ValueError(f"Source directory not found: {source_path}")

        # Step 1: Scan files and build tree
        print(f"[BFS] Scanning directory: {source_path}")
        files_on_disk = scan_directory(source_path, max_depth, self.exclude_dirs)
        print(f"[BFS] Found {len(files_on_disk)} files")

        # Step 2: Load descriptions from database
        path2desc = load_descriptions(db_path)
        if path2desc:
            print(f"[BFS] Loaded {len(path2desc)} descriptions from database")

        # Step 3: Build folder tree
        root = build_tree(source_path, files_on_disk, path2desc)
        print(f"[BFS] Built tree with {len(root.children)} top-level folders")

        # Step 4: BFS traversal and classification
        result = self._bfs_classify(root, path2desc)

        print(f"[BFS] Classification complete:")
        print(f"      - {result.files_classified_via_folder} files classified via folder")
        print(f"      - {result.files_classified_individually} files classified individually")
        print(f"      - {len(result.skipped_folders)} folders skipped")

        return result

    def _bfs_classify(
        self,
        root: FolderNode,
        db_context: Dict[str, str],
    ) -> TraversalResult:
        """
        Core BFS classification algorithm.

        Algorithm (t2c2spr):
        1. Initialize task queue with root's children (top-level folders)
        2. While queue is not empty:
           a. Dequeue next item (folder or file)
           b. Classify using classifier
           c. If folder:
              - Record folder classification
              - If Skip OR low confidence OR mixed: enqueue children
              - Else: assign category to all contents (folder-level decision)
           d. If file:
              - Record file classification
        3. Return all recorded classifications
        """
        classifications: Dict[str, Classification] = {}
        folder_decisions: Dict[str, ClassificationResult] = {}
        skipped_folders: List[str] = []
        files_via_folder = 0
        files_individual = 0

        # Task queue: can contain FolderNode or FileMeta
        task_queue: deque[Union[FolderNode, FileMeta]] = deque()

        # Initialize with top-level folders (not the root itself)
        for child in root.children.values():
            task_queue.append(child)

        # Also add root-level files (files directly in course root)
        for file_meta in root.files:
            task_queue.append(file_meta)

        while task_queue:
            item = task_queue.popleft()

            if isinstance(item, FolderNode):
                # Classify folder
                result = self.classifier.classify_folder(item, db_context)
                folder_decisions[item.path] = result

                print(f"[BFS] Folder '{item.path}': {result.category.value} "
                      f"(confidence={result.confidence:.2f}, mixed={result.is_mixed})")

                # Decision logic
                should_descend = (
                    result.category == Category.SKIP or
                    not result.is_confident(self.confidence_threshold) or
                    result.is_mixed
                )

                if result.category == Category.SKIP:
                    skipped_folders.append(item.path)
                    # Record folder as skipped but don't process children
                    classifications[item.path] = Classification(
                        path=item.path,
                        category=Category.SKIP,
                        confidence=result.confidence,
                        reason=result.reason,
                        classified_at_level="folder",
                    )
                    # Note: we skip the contents entirely

                elif should_descend:
                    # Low confidence or mixed - descend to children
                    print(f"[BFS]   -> Descending into '{item.path}' for finer-grained classification")

                    # Record the folder decision but note it wasn't final
                    classifications[item.path] = Classification(
                        path=item.path,
                        category=result.category,
                        confidence=result.confidence,
                        reason=result.reason + " [descended for file-level classification]",
                        classified_at_level="folder",
                    )

                    # Enqueue all subfolders
                    for child in item.children.values():
                        task_queue.append(child)

                    # Enqueue all files in this folder
                    for file_meta in item.files:
                        task_queue.append(file_meta)

                else:
                    # Confident folder-level decision - assign to all contents
                    print(f"[BFS]   -> Confident classification, assigning to all contents")

                    # Record folder classification
                    classifications[item.path] = Classification(
                        path=item.path,
                        category=result.category,
                        confidence=result.confidence,
                        reason=result.reason,
                        classified_at_level="folder",
                    )

                    # Assign category to all files under this folder
                    all_files = self._collect_all_files(item)
                    for file_meta in all_files:
                        classifications[file_meta.source_path] = Classification(
                            path=file_meta.source_path,
                            category=result.category,
                            confidence=result.confidence,
                            reason=f"Inherited from folder '{item.path}': {result.reason}",
                            classified_at_level="folder",
                            parent_folder=item.path,
                        )
                        files_via_folder += 1

            elif isinstance(item, FileMeta):
                # Classify individual file
                result = self.classifier.classify_file(item, db_context)

                print(f"[BFS] File '{item.source_path}': {result.category.value} "
                      f"(confidence={result.confidence:.2f})")

                # Skip files get SKIP category
                if result.category == Category.SKIP:
                    # Skip this file - don't add to classifications
                    continue

                classifications[item.source_path] = Classification(
                    path=item.source_path,
                    category=result.category,
                    confidence=result.confidence,
                    reason=result.reason,
                    classified_at_level="file",
                )
                files_individual += 1

        return TraversalResult(
            classifications=classifications,
            folder_decisions=folder_decisions,
            skipped_folders=skipped_folders,
            files_classified_individually=files_individual,
            files_classified_via_folder=files_via_folder,
        )

    def _collect_all_files(self, node: FolderNode) -> List[FileMeta]:
        """Collect all files under a folder recursively (BFS order)."""
        files: List[FileMeta] = []
        queue: deque[FolderNode] = deque([node])

        while queue:
            current = queue.popleft()
            files.extend(current.files)
            for child in current.children.values():
                queue.append(child)

        return files


# ============================ Utility Functions ============================

def traverse_to_classify(
    source_path: str,
    db_path: Optional[str] = None,
    classifier: Optional[Classifier] = None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> TraversalResult:
    """
    Convenience function to perform BFS traversal and classification.

    This is the main entry point for the t2c2spr algorithm:
    Traverse-to-Classify-to-(Study/Practice/Support/Skip)

    Args:
        source_path: Root directory of the course materials
        db_path: Optional path to SQLite database with file descriptions
        classifier: Optional classifier instance (uses PlaceholderClassifier if None)
        confidence_threshold: Minimum confidence for folder-level classification

    Returns:
        TraversalResult with all classifications

    Example:
        result = traverse_to_classify(
            source_path="/path/to/cs61a",
            db_path="/path/to/metadata.db",
        )

        for path, classification in result.classifications.items():
            if classification.category != Category.SKIP:
                print(f"{path} -> {classification.category.value}")
    """
    traverser = BFSTraverser(
        classifier=classifier,
        confidence_threshold=confidence_threshold,
    )
    return traverser.traverse(source_path, db_path)


def print_classification_summary(result: TraversalResult) -> None:
    """Print a summary of classification results."""
    print("\n" + "=" * 70)
    print("CLASSIFICATION SUMMARY")
    print("=" * 70)

    # Count by category
    category_counts: Dict[Category, int] = {cat: 0 for cat in Category}
    for classification in result.classifications.values():
        category_counts[classification.category] += 1

    print("\nBy Category:")
    for cat, count in category_counts.items():
        print(f"  {cat.value:10s}: {count}")

    print(f"\nClassification Method:")
    print(f"  Via folder:     {result.files_classified_via_folder}")
    print(f"  Individually:   {result.files_classified_individually}")

    print(f"\nSkipped Folders: {len(result.skipped_folders)}")
    for folder in result.skipped_folders:
        print(f"  - {folder}")

    print("=" * 70)


# ============================ CLI Entry Point ============================

def main():
    """CLI entry point for testing BFS traversal."""
    import argparse

    parser = argparse.ArgumentParser(
        description="BFS Traversal and Classification for Course Materials",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--source", "-s",
        required=True,
        help="Source directory to traverse",
    )
    parser.add_argument(
        "--db", "-d",
        default=None,
        help="SQLite database path for file descriptions",
    )
    parser.add_argument(
        "--threshold", "-t",
        type=float,
        default=DEFAULT_CONFIDENCE_THRESHOLD,
        help=f"Confidence threshold for folder-level classification (default: {DEFAULT_CONFIDENCE_THRESHOLD})",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print detailed classification for each file",
    )

    args = parser.parse_args()

    print("=" * 70)
    print("BFS TRAVERSAL v1 - Course Material Classifier")
    print("=" * 70)
    print(f"Source:    {args.source}")
    print(f"DB:        {args.db or '(none)'}")
    print(f"Threshold: {args.threshold}")
    print("=" * 70)

    result = traverse_to_classify(
        source_path=args.source,
        db_path=args.db,
        confidence_threshold=args.threshold,
    )

    print_classification_summary(result)

    if args.verbose:
        print("\nDetailed Classifications:")
        print("-" * 70)
        for path, classification in sorted(result.classifications.items()):
            print(f"{classification.category.value:10s} | {path}")
            print(f"           | Confidence: {classification.confidence:.2f}")
            print(f"           | Level: {classification.classified_at_level}")
            if classification.parent_folder:
                print(f"           | Parent: {classification.parent_folder}")
            print()


if __name__ == "__main__":
    main()
