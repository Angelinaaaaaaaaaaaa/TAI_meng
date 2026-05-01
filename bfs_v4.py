#!/usr/bin/env python3
"""
bfs_v4.py

BFS-based File Reorganization Agent — v4.
python bfs_v4.py \
  --source "106B/EECS 106B" \
  --db "106B/collective_metadata.db" \
  --model "gpt-5-mini-2025-08-07" \
  --execute \
  --dest "106B/EECS 106B_out"

Edits:
  - No MAX_ANCESTOR_DEPTH or truncation for concatenated descriptions
  - Study destination has NO "lecture/" injection:
      study/<top_folder>/<tail>
  - Default model remains: gpt-5-mini-2025-08-07
  - File classification outputs are only: study/practice/support (handled in classify_v4.py).
"""

import json
import logging
import os
import re
import shutil
import sqlite3
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple, Union

from classify_v4 import (
    LLMClassifier,
    APITimeoutError,
    Category,
    FileMeta,
    FolderNode,
    FileIndexEntry,
    FolderStats,
    ClassificationResult,
    collect_all_files,
)

logger = logging.getLogger(__name__)


# ====================================================================
#  Constants
# ====================================================================

HARD_SKIP_DIRS: Set[str] = {
    ".git", "__pycache__", "node_modules",
    ".DS_Store", ".ipynb_checkpoints",
    "venv", ".venv", "env", ".env",
    ".tox", ".mypy_cache", ".pytest_cache",
    "build", "dist", ".eggs",
}

DEFAULT_DB_PATH = "file.db"
LLM_DEBUG_LOG_FILE = "bfs_v4_llm_debug.json"
REPORT_MD_FILE = "bfs_v4_report.md"
PLAN_JSON_FILE = "bfs_v4_plan.json"
TREE_JSON_FILE = "bfs_v4_tree.json"
MAX_SEQUENCE_CONTEXT_ITEMS = 80
MAX_SEQUENCE_DESCRIPTION_CHARS = 240
MAX_SEQUENCE_ITEM_DESCRIPTION_CHARS = 360


### YK's Modification: add TASK_KEYWORDS and priorities according to task/seq classification rules in classify_v4.py to cover more edge cases that may be missed by the model, especially for files with sparse descriptions. This is a simple keyword-based heuristic that runs before the LLM classification, and can override the category to practice/study/support based on the presence of certain keywords in the file name or description.
PRESET_TASK_NAMES: Set[str] = {
    "announcements",
    "administrative",
    "boardwork",
    "discussion",
    "homework",
    "lab",
    "slides",
    "project",
    "quiz",
    "resources",
}
TASK_KEYWORDS = set(PRESET_TASK_NAMES) # added known keywords for covering missing cases.
TASK_NAME_ALIASES: Dict[str, Set[str]] = {
    "discussion": {"disc", "section", "worksheet"},
    "homework": {"hw"},
    "lecture": {"lec"},
    "project": {"proj"},
}

def normalize_task_name(raw: Optional[str]) -> Optional[str]:
    """Normalize task labels without applying a preset allow/deny list."""
    return (raw or "").strip().lower() or None


def normalize_task_name_for_category(
    raw: Optional[str],
    category: Optional[str],
) -> Optional[str]:
    """Normalize task labels and drop broad category names used as tasks."""
    task = normalize_task_name(raw)
    cat = (category or "").strip().lower()
    if task and task == cat:
        return None
    if task in {"study", "practice", "support", "skip"}:
        return None
    return task


def explicit_task_name_from_source(source_rel: str) -> Optional[str]:
    """Return a preset task when filename tokens explicitly match one."""
    stem = os.path.splitext(os.path.basename(source_rel))[0].lower()
    tokens = {token for token in re.split(r"[^a-z0-9]+", stem) if token}
    preset_matches = sorted(tokens & PRESET_TASK_NAMES)
    if preset_matches:
        return preset_matches[0]

    for task_name, aliases in TASK_NAME_ALIASES.items():
        if task_name not in PRESET_TASK_NAMES:
            continue
        alias_pattern = re.compile(
            r"^(?:" + "|".join(re.escape(alias) for alias in sorted(aliases)) + r")\d+[a-z]?$"
        )
        if tokens & aliases or any(alias_pattern.fullmatch(token) for token in tokens):
            return task_name
    return None


def task_name_from_source_context(
    source_rel: str,
    category: Optional[str],
    fallback: Optional[str] = None,
) -> Optional[str]:
    """Apply deterministic source-path task rules before falling back."""
    explicit = explicit_task_name_from_source(source_rel)
    if explicit:
        return explicit

    if (category or "").lower() == Category.STUDY.value:
        folders = source_rel.replace("\\", "/").split("/")[:-1]
        if any(part.lower() in {"lec", "lecture", "lectures"} for part in folders):
            return "slides"

    return fallback


def folder_has_explicit_file_task_split(
    files: List[FileMeta],
    inherited_task_name: Optional[str],
) -> bool:
    """Return true when direct folder assignment would hide explicit file tasks."""
    explicit_tasks = {
        task
        for f in files
        for task in [explicit_task_name_from_source(f.source_path)]
        if task
    }
    if not explicit_tasks:
        return False
    inherited = normalize_task_name(inherited_task_name)
    return any(task != inherited for task in explicit_tasks)


def compact_text(text: Optional[str], max_chars: int) -> str:
    """Normalize whitespace and cap long metadata before sending it to the LLM."""
    value = re.sub(r"\s+", " ", text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip() + "..."


def category_depth_for(
    source_rel: str,
    task_name: Optional[str],
    sequence_name: Optional[str],
    model_depth: int = 0,
) -> int:
    """Deepest source path folder level where task/sequence metadata appears."""
    parts = [p for p in source_rel.replace("\\", "/").split("/") if p]
    is_file = "." in os.path.basename(source_rel)
    filename = os.path.basename(source_rel) if is_file else ""
    filename_stem = os.path.splitext(filename)[0].lower()
    folder_parts = parts[:-1] if is_file else parts

    deepest = 0
    targets = [v.lower() for v in (task_name, sequence_name) if v]
    for i, part in enumerate(folder_parts, 1):
        if part.lower() in targets:
            deepest = max(deepest, i)

    if is_file and targets:
        normalized_stem = "".join(ch for ch in filename_stem if ch.isalnum())
        for target in targets:
            normalized_target = "".join(ch for ch in target if ch.isalnum())
            if target in filename_stem or (
                normalized_target and normalized_target in normalized_stem
            ):
                deepest = max(deepest, len(folder_parts))

    if model_depth > 0:
        deepest = max(deepest, model_depth)

    if targets and deepest == 0:
        deepest = len(folder_parts)

    return min(deepest, len(folder_parts))



# ====================================================================
#  Additional Data Structures (BFS-specific)
# ====================================================================

@dataclass
class Classification:
    """Final classification record."""
    path: str
    category: Category
    reason: str
    classified_at_level: str    # "folder" or "file"
    parent_folder: Optional[str] = None
    ancestor_descriptions: List[str] = field(default_factory=list)


@dataclass
class FileMapping:
    """Planned move for one file."""
    source_rel: str
    dest_rel: str
    top_folder: str
    category: str
    reason: str
    ## YK's Modification: add task_name and seq_name to the final classification result for more granular report generation
    task_name: Optional[str] = None
    sequence_name: Optional[str] = None
    category_depth: int = 0


@dataclass
class TraversalResult:
    """Complete output of the BFS pipeline."""
    classifications: Dict[str, Classification]
    folder_decisions: Dict[str, ClassificationResult]
    skipped_folders: List[str]
    mappings: Dict[str, FileMapping]    # keyed by source_rel
    files_classified_individually: int
    files_classified_via_folder: int
    # Sync stats
    files_on_disk_count: int = 0
    files_missing_in_db: List[str] = field(default_factory=list)
    files_stale_in_db: List[str] = field(default_factory=list)
    # Tree root (for tree JSON export)
    root_node: Optional[FolderNode] = None
    


# ====================================================================
#  CourseDB — Database Connection & File Index
# ====================================================================

class CourseDB:
    """
    Manages the SQLite database connection (file.db) and provides
    an indexed view of all file metadata.
    """

    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(db_path)
        self.conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"Database not found: {self.db_path}")

        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

        try:
            cur = self.conn.cursor()
            cur.execute("SELECT uuid, file_name, description FROM file LIMIT 1")
            logger.info(f"[DB] Connected to {self.db_path}")
        except sqlite3.OperationalError as e:
            self.conn.close()
            self.conn = None
            raise RuntimeError(
                f"Database at {self.db_path} missing expected 'file' table: {e}"
            )

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

    def _ensure_connected(self) -> sqlite3.Connection:
        if self.conn is None:
            raise RuntimeError("Database not connected. Call db.connect() first.")
        return self.conn

    def load_file_index(self) -> Dict[str, FileIndexEntry]:
        """Load all files from DB into an index keyed by file_name."""
        conn = self._ensure_connected()
        cur = conn.cursor()
        cur.execute(
            "SELECT uuid, file_name, description, relative_path, "
            "relative_path, extra_info, file_hash FROM file"
        )
        rows = cur.fetchall()

        index: Dict[str, FileIndexEntry] = {}
        for r in rows:
            fname = r["file_name"] or ""
            if not fname:
                rel = r["relative_path"] or ""
                fname = os.path.basename(rel) if rel else ""
            if not fname:
                continue

            entry = FileIndexEntry(
                uuid=r["uuid"],
                file_name=fname,
                description=r["description"] or "",
                original_path=r["relative_path"],
                relative_path=r["relative_path"],
                extra_info=r["extra_info"] or "",
                file_hash=r["file_hash"] or None,
            )
            if fname not in index:
                index[fname] = entry

        logger.info(f"[DB] Loaded {len(index)} file entries")
        return index

    def get_uuids_for_files(self, file_names: List[str]) -> List[str]:
        conn = self._ensure_connected()
        uuids: List[str] = []
        cur = conn.cursor()
        for fname in file_names:
            cur.execute("SELECT uuid FROM file WHERE file_name = ?", (fname,))
            row = cur.fetchone()
            if row:
                uuids.append(row["uuid"])
        return uuids

    def update_folder_description_bulk(
        self, uuids: List[str], folder_description: str
    ) -> int:
        """
        Write folder_description into extra_info for all files with given UUIDs.
        Only called on confident non-SKIP folder assignments.
        """
        conn = self._ensure_connected()
        cur = conn.cursor()
        updated = 0

        for uuid in uuids:
            cur.execute("SELECT extra_info FROM file WHERE uuid = ?", (uuid,))
            row = cur.fetchone()
            if not row:
                continue

            raw = row["extra_info"]

            if not raw:
                info = {}
            else:
                try:
                    parsed = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    parsed = None

                if isinstance(parsed, dict):
                    info = parsed
                elif isinstance(parsed, list):
                    info = {"_original": parsed}
                else:
                    info = {}

            info["folder_description"] = folder_description

            cur.execute(
                "UPDATE file SET extra_info = ?, update_time = ? WHERE uuid = ?",
                (json.dumps(info, ensure_ascii=False), datetime.now().isoformat(), uuid),
            )
            updated += 1

        conn.commit()
        logger.info(f"[DB] Updated folder_description for {updated}/{len(uuids)} files")
        return updated


# ====================================================================
#  Tree Building & Utility Functions
# ====================================================================

def scan_directory(
    root_dir: str,
    max_depth: Optional[int] = None,
    hard_skip_dirs: Optional[Set[str]] = None,
) -> List[str]:
    """Scan all files under root_dir, returning relative paths."""
    out: List[str] = []
    root_dir = os.path.abspath(root_dir)
    hard_skip_dirs = hard_skip_dirs or HARD_SKIP_DIRS

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
            if d not in hard_skip_dirs and not d.startswith(".")
        ]

        for fn in sorted(filenames):
            if fn.startswith("."):
                continue
            full = os.path.join(cur_root, fn)
            rel = os.path.relpath(full, root_dir).replace("\\", "/")
            out.append(rel)

    return out


def build_tree(
    _root_dir: str,
    files_on_disk: List[str],
    file_index: Dict[str, FileIndexEntry],
) -> FolderNode:
    """Build a FolderNode tree and attach DB descriptions to files."""
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

        entry = file_index.get(fname)
        desc = entry.description if entry else None
        fhash = entry.file_hash if entry else None

        node.files.append(FileMeta(
            source_path=rel_path,
            folder_path=folder,
            file_name=fname,
            description=desc,
            file_hash=fhash,
        ))

    return root


def compute_folder_stats(node: FolderNode) -> FolderStats:
    """Compute structural statistics for a folder."""
    all_files = collect_all_files(node)

    ext_counts: Dict[str, int] = {}
    for f in all_files:
        ext = os.path.splitext(f.file_name)[1].lower() or "(no ext)"
        ext_counts[ext] = ext_counts.get(ext, 0) + 1

    sorted_exts = sorted(ext_counts.items(), key=lambda x: -x[1])
    primary = [e for e, _ in sorted_exts[:3]]

    metadata_exts = {".yaml", ".yml", ".json", ".md", ".txt", ".toml", ".ini"}
    content_exts = {e for e in ext_counts if e not in metadata_exts}
    is_homogeneous = len(content_exts) <= 1

    return FolderStats(
        total_file_count=len(all_files),
        immediate_file_count=len(node.files),
        subfolder_count=len(node.children),
        subfolder_names=sorted(node.children.keys()),
        extension_counts=ext_counts,
        is_homogeneous=is_homogeneous,
        primary_extensions=primary,
    )


def build_concat_desc(
    files: List[FileMeta],
    file_index: Dict[str, FileIndexEntry],
) -> str:
    """Build a concatenated description string (NO truncation)."""
    parts: List[str] = []
    for f in files:
        entry = file_index.get(f.file_name)
        desc = ""
        if entry and entry.description:
            desc = entry.description.replace("\n", " ").strip()
        elif f.description:
            desc = f.description.replace("\n", " ").strip()
        if not desc:
            continue
        parts.append(f"{f.file_name}: {desc}")
    return "\n".join(parts)


def top_level_folder(source_path: str) -> str:
    """Extract top-level folder from a relative path."""
    parts = source_path.replace("\\", "/").split("/")
    return parts[0] if len(parts) > 1 else ""


def build_dest_rel(
    category: str,
    source_rel: str,
    task_name: Optional[str] = None,
    sequence_name: Optional[str] = None,
    category_depth: int = 0,
) -> str:
    """
    Build destination path:
      {category}/{task_name}/{sequence_name}/{source suffix deeper than category_depth}
    """
    source_parts = [p for p in source_rel.replace("\\", "/").split("/") if p]
    if not source_parts:
        return category

    parts = [category]
    if task_name:
        parts.append(task_name)
    if sequence_name:
        parts.append(sequence_name)

    depth = max(category_depth, 0)
    source_includes_category = source_parts[0].lower() == category.lower()
    suffix_start = depth + 1 if source_includes_category else depth
    suffix_start = min(suffix_start, len(source_parts) - 1)
    suffix = source_parts[suffix_start:]
    if not suffix:
        suffix = [source_parts[-1]]

    parts.extend(suffix)
    return _join_rel(*parts)


def _join_rel(*parts: str) -> str:
    clean = [p.strip().replace("\\", "/") for p in parts if p and p.strip()]
    return "/".join(clean)


def compute_sync_stats(
    file_index: Dict[str, FileIndexEntry],
    files_on_disk: List[str],
) -> tuple:
    """Compute DB/disk mismatches and log warnings."""
    disk_filenames = {os.path.basename(f) for f in files_on_disk}
    db_filenames = set(file_index.keys())

    missing_in_db = sorted(disk_filenames - db_filenames)
    stale_in_db = sorted(db_filenames - disk_filenames)

    if missing_in_db:
        logger.warning(
            f"[SYNC] {len(missing_in_db)} files on disk have NO DB entry. "
            f"Examples: {missing_in_db[:5]}"
        )
    if stale_in_db:
        logger.warning(
            f"[SYNC] {len(stale_in_db)} DB entries have no file on disk. "
            f"Examples: {stale_in_db[:5]}"
        )
    if not missing_in_db and not stale_in_db:
        logger.info("[SYNC] DB and disk are fully in sync.")

    return missing_in_db, stale_in_db


# ====================================================================
#  BFS Traverser
# ====================================================================

class BFSTraverser:
    """Core BFS traversal engine."""

    def __init__(
        self,
        db: CourseDB,
        classifier: LLMClassifier,
    ):
        self.db = db
        self.classifier = classifier

    def traverse(
        self,
        source_path: str,
        max_depth: Optional[int] = None,
    ) -> TraversalResult:
        """Main entry point: run the full BFS pipeline."""
        source_path = os.path.abspath(source_path)
        if not os.path.isdir(source_path):
            raise ValueError(f"Source directory not found: {source_path}")

        file_index = self.db.load_file_index()

        logger.info(f"[BFS] Scanning: {source_path}")
        files_on_disk = scan_directory(source_path, max_depth)
        logger.info(f"[BFS] Found {len(files_on_disk)} files on disk")

        missing_in_db, stale_in_db = compute_sync_stats(file_index, files_on_disk)

        root = build_tree(source_path, files_on_disk, file_index)
        logger.info(f"[BFS] Tree: {len(root.children)} top-level folders")

        result = self._bfs_classify(root, file_index)

        result.files_on_disk_count = len(files_on_disk)
        result.files_missing_in_db = missing_in_db
        result.files_stale_in_db = stale_in_db
        result.root_node = root  # Store for tree JSON export

        logger.info(
            f"[BFS] Done: {result.files_classified_via_folder} via folder, "
            f"{result.files_classified_individually} individually, "
            f"{len(result.skipped_folders)} skipped, "
            f"{len(result.mappings)} mappings"
        )

        self.classifier.save_debug_log(LLM_DEBUG_LOG_FILE)
        return result

    def _bfs_classify(
        self,
        root: FolderNode,
        file_index: Dict[str, FileIndexEntry],
    ) -> TraversalResult:
        """Core BFS loop."""
        classifications: Dict[str, Classification] = {}
        folder_decisions: Dict[str, ClassificationResult] = {}
        skipped_folders: List[str] = []
        mappings: Dict[str, FileMapping] = {}

        seen: Set[str] = set()
        known_task_names: Set[str] = set(PRESET_TASK_NAMES)
        ancestor_desc_map: Dict[str, List[str]] = {}
        # Maps folder_path -> (task_name, sequence_name) derived structurally
        task_context_map: Dict[str, tuple] = {}
        task_queue: deque[Union[FolderNode, FileMeta]] = deque()

        for child in root.children.values():
            task_queue.append(child)
            ancestor_desc_map[child.path] = []
            task_context_map[child.path] = (None, None)
        for f in root.files:
            task_queue.append(f)

        while task_queue:
            item = task_queue.popleft()

            item_key = item.source_path if isinstance(item, FileMeta) else item.path
            if item_key in seen:
                continue
            seen.add(item_key)

            if isinstance(item, FolderNode):
                self._process_folder(
                    item, file_index, ancestor_desc_map, task_context_map,
                    task_queue, seen,
                    classifications, folder_decisions, skipped_folders, mappings,
                    known_task_names,
                )
            elif isinstance(item, FileMeta):
                self._process_file(
                    item, file_index, ancestor_desc_map, task_context_map,
                    classifications, mappings,
                    known_task_names,
                )

        files_via_folder = sum(
            1 for c in classifications.values()
            if c.classified_at_level == "folder" and c.parent_folder is not None
        )
        files_individual = sum(
            1 for c in classifications.values()
            if c.classified_at_level == "file"
        )

        return TraversalResult(
            classifications=classifications,
            folder_decisions=folder_decisions,
            skipped_folders=skipped_folders,
            mappings=mappings,
            files_classified_individually=files_individual,
            files_classified_via_folder=files_via_folder,
        )

    def _process_folder(
        self,
        item: FolderNode,
        file_index: Dict[str, FileIndexEntry],
        ancestor_desc_map: Dict[str, List[str]],
        task_context_map: Dict[str, tuple],
        task_queue: deque,
        _seen: Set[str],
        classifications: Dict[str, Classification],
        folder_decisions: Dict[str, ClassificationResult],
        skipped_folders: List[str],
        mappings: Dict[str, FileMapping],
        known_task_names: Optional[Set[str]] = None,
    ) -> None:
        my_ancestors = ancestor_desc_map.get(item.path, [])
        my_task_name, my_seq_name = task_context_map.get(item.path, (None, None))

        folder_files = collect_all_files(item)
        folder_stats = compute_folder_stats(item)
        concat_desc = build_concat_desc(folder_files, file_index)

        result = self.classifier.classify_folder(
            item, file_index, folder_stats, concat_desc,
            ancestor_descriptions=my_ancestors,
        )
        if result.category != Category.SKIP and not result.is_mixed:
            task_result = self.classifier.refer_folder_task_sequence(
                item, file_index, folder_stats, concat_desc,
                category=result.category,
                folder_description=result.folder_description,
                ancestor_descriptions=my_ancestors,
                known_task_names=known_task_names,
            )
            result.task_name = normalize_task_name_for_category(
                task_result.task_name,
                result.category.value,
            )
            if result.task_name and known_task_names is not None:
                known_task_names.add(result.task_name)
            result.sequence_name = task_result.sequence_name
            result.category_depth = task_result.category_depth
            result.by_type = task_result.by_type
        folder_decisions[item.path] = result

        logger.info(
            f"[BFS] Folder '{item.path}': {result.category.value} "
            f"(mixed={result.is_mixed}, by_type={result.by_type}, by_sequence={result.by_sequence}, task_name={result.task_name}, sequence_name={result.sequence_name})"
        )

        # Accumulate ancestor context (NO depth cap)
        child_ancestors = list(my_ancestors)
        if result.folder_description:
            child_ancestors.append(result.folder_description)

        # --- Derive task context for child folders structurally ---
        # by_type=True  → each child's name IS the task_name
        # Sequence names are inferred after BFS; children otherwise inherit parent's task context.
        current_task_name = result.task_name or my_task_name
        current_seq_name = result.sequence_name or my_seq_name

        def _child_task_ctx(child_name: str) -> tuple:
            if result.by_type:
                child_task_name = normalize_task_name(child_name)
                if child_task_name and known_task_names is not None:
                    known_task_names.add(child_task_name)
                return (child_task_name, None)
            return (current_task_name, current_seq_name)

        # SKIP: always descend
        if result.category == Category.SKIP:
            skipped_folders.append(item.path)
            classifications[item.path] = Classification(
                path=item.path,
                category=Category.SKIP,
                reason=result.reason,
                classified_at_level="folder",
                ancestor_descriptions=list(my_ancestors),
            )
            logger.info(
                f"[BFS]   -> SKIP, descending with {len(child_ancestors)} parent-level context entries"
            )
            for child in item.children.values():
                ancestor_desc_map[child.path] = child_ancestors
                task_context_map[child.path] = _child_task_ctx(child.name)
                task_queue.append(child)
            ancestor_desc_map[item.path] = child_ancestors
            for f in item.files:
                task_context_map[f.source_path] = (current_task_name, current_seq_name)
                task_queue.append(f)
            return

        # Mixed: treat like SKIP (descend, no DB write)
        if result.is_mixed:
            skipped_folders.append(item.path)
            classifications[item.path] = Classification(
                path=item.path,
                category=result.category,
                reason=result.reason + " [mixed→skip, descended]",
                classified_at_level="folder",
                ancestor_descriptions=list(my_ancestors),
            )
            logger.info(
                f"[BFS]   -> Mixed, descending with {len(child_ancestors)} parent-level context entries"
            )
            for child in item.children.values():
                ancestor_desc_map[child.path] = child_ancestors
                task_context_map[child.path] = _child_task_ctx(child.name)
                task_queue.append(child)
            ancestor_desc_map[item.path] = child_ancestors
            for f in item.files:
                task_context_map[f.source_path] = (current_task_name, current_seq_name)
                task_queue.append(f)
            return

        # Organizer folders should be descended into so child folders/files can get
        # their own category. Otherwise containers like assets/ stamp all children
        # as support even when they contain lec/, hw/, disc/, proj/, etc.
        if result.by_type:
            classifications[item.path] = Classification(
                path=item.path,
                category=result.category,
                reason=result.reason + " [by_type organizer, descended]",
                classified_at_level="folder",
                ancestor_descriptions=list(my_ancestors),
            )
            logger.info(
                f"[BFS]   -> by_type organizer, descending with {len(child_ancestors)} parent-level context entries"
            )
            for child in item.children.values():
                ancestor_desc_map[child.path] = child_ancestors
                task_context_map[child.path] = _child_task_ctx(child.name)
                task_queue.append(child)
            ancestor_desc_map[item.path] = child_ancestors
            for f in item.files:
                task_context_map[f.source_path] = (current_task_name, current_seq_name)
                task_queue.append(f)
            return

        if folder_has_explicit_file_task_split(folder_files, current_task_name):
            result.task_name = None
            result.sequence_name = None
            result.category_depth = 0
            classifications[item.path] = Classification(
                path=item.path,
                category=result.category,
                reason=result.reason + " [explicit file task split, descended]",
                classified_at_level="folder",
                ancestor_descriptions=list(my_ancestors),
            )
            logger.info(
                f"[BFS]   -> Explicit file task split, descending with {len(child_ancestors)} parent-level context entries"
            )
            for child in item.children.values():
                ancestor_desc_map[child.path] = child_ancestors
                task_context_map[child.path] = _child_task_ctx(child.name)
                task_queue.append(child)
            ancestor_desc_map[item.path] = child_ancestors
            for f in item.files:
                task_context_map[f.source_path] = (current_task_name, current_seq_name)
                task_queue.append(f)
            return

        # Non-SKIP, non-mixed: assign folder directly
        classifications[item.path] = Classification(
            path=item.path,
            category=result.category,
            reason=result.reason,
            classified_at_level="folder",
            ancestor_descriptions=list(my_ancestors),
        )

        logger.info(f"[BFS]   -> Assigning {len(folder_files)} files directly")

        # Write to DB only on confident non-SKIP assignment
        if result.folder_description:
            file_names = [f.file_name for f in folder_files]
            uuids = self.db.get_uuids_for_files(file_names)
            if uuids:
                self.db.update_folder_description_bulk(uuids, result.folder_description)

        for f in folder_files:
            classifications[f.source_path] = Classification(
                path=f.source_path,
                category=result.category,
                reason=f"Inherited from folder '{item.path}': {result.reason}",
                classified_at_level="folder",
                parent_folder=item.path,
                ancestor_descriptions=list(child_ancestors),
            )

            top = top_level_folder(f.source_path)

            # Direct folder assignment stamps all descendants with this folder's
            # task/sequence context.
            file_task = task_name_from_source_context(
                f.source_path,
                result.category.value,
                current_task_name,
            )
            file_seq = current_seq_name
            if file_task != current_task_name:
                file_seq = None
                if known_task_names is not None:
                    known_task_names.add(file_task)
            category_depth = category_depth_for(
                f.source_path,
                file_task,
                file_seq,
                result.category_depth,
            )
            dest_rel = build_dest_rel(
                result.category.value,
                f.source_path,
                file_task,
                file_seq,
                category_depth,
            )

            mappings[f.source_path] = FileMapping(
                source_rel=f.source_path,
                dest_rel=dest_rel,
                top_folder=top,
                category=result.category.value,
                reason=result.reason,
                task_name=file_task,
                sequence_name=file_seq,
                category_depth=category_depth,
            )

    def _process_file(
        self,
        item: FileMeta,
        file_index: Dict[str, FileIndexEntry],
        ancestor_desc_map: Dict[str, List[str]],
        task_context_map: Dict[str, tuple],
        classifications: Dict[str, Classification],
        mappings: Dict[str, FileMapping],
        known_task_names: Optional[Set[str]] = None,
    ) -> None:
        file_ancestors = ancestor_desc_map.get(item.folder_path, [])

        try:
            result = self.classifier.classify_file(
                item, file_index,
                ancestor_descriptions=file_ancestors,
            )
            if result.category != Category.SKIP:
                task_result = self.classifier.refer_file_task_sequence(
                    item, file_index,
                    category=result.category,
                    ancestor_descriptions=file_ancestors,
                    known_task_names=known_task_names,
                )
                result.task_name = normalize_task_name_for_category(
                    task_result.task_name,
                    result.category.value,
                )
                if result.task_name and known_task_names is not None:
                    known_task_names.add(result.task_name)
                result.sequence_name = task_result.sequence_name
                result.category_depth = task_result.category_depth
        except APITimeoutError:
            logger.warning(
                f"[BFS] File '{item.source_path}': timed out during classification; skipping"
            )
            return

        logger.info(
            f"[BFS] File '{item.source_path}': {result.category.value}"
        )

        # classify_v4 guarantees file category is only study/practice/support
        classifications[item.source_path] = Classification(
            path=item.source_path,
            category=result.category,
            reason=result.reason,
            classified_at_level="file",
            ancestor_descriptions=list(file_ancestors),
        )
        top = top_level_folder(item.source_path)

        # Inherit task/sequence context from folder (or file-specific entry set during skip/mixed)
        file_task, file_seq = task_context_map.get(
            item.source_path,
            task_context_map.get(item.folder_path, (None, None))
        )
        file_task = result.task_name or file_task
        file_seq = result.sequence_name or file_seq
        contextual_task = task_name_from_source_context(
            item.source_path,
            result.category.value,
            file_task,
        )
        if contextual_task != file_task:
            file_task = contextual_task
            file_seq = None
            if known_task_names is not None:
                known_task_names.add(file_task)
        category_depth = category_depth_for(
            item.source_path,
            file_task,
            file_seq,
            result.category_depth,
        )
        dest_rel = build_dest_rel(
            result.category.value,
            item.source_path,
            file_task,
            file_seq,
            category_depth,
        )

        mappings[item.source_path] = FileMapping(
            source_rel=item.source_path,
            dest_rel=dest_rel,
            top_folder=top,
            category=result.category.value,
            reason=result.reason,
            task_name=file_task,
            sequence_name=file_seq,
            category_depth=category_depth,
        )


# ====================================================================
#  Tree JSON Export
# ====================================================================

def _build_class_tree(
    node: FolderNode,
    classifications: Dict[str, Classification],
    folder_decisions: Dict[str, ClassificationResult],
) -> dict:
    node_dict: dict = {
        "path": node.path,
        "name": node.name,
        "type": "folder",
    }

    fd = folder_decisions.get(node.path)
    if fd:
        node_dict["category"] = fd.category.value
        node_dict["is_mixed"] = fd.is_mixed
        node_dict["folder_description"] = fd.folder_description
        node_dict["by_type"] = fd.by_type
        node_dict["by_sequence"] = fd.by_sequence

    files_dict: dict = {}
    for f in node.files:
        fc = classifications.get(f.source_path)
        entry: dict = {
            "path": f.source_path,
            "name": f.file_name,
            "type": "file",
            "file_hash": f.file_hash,
        }
        if fc:
            entry["category"] = fc.category.value
            entry["classified_by"] = fc.classified_at_level
        key = f.file_hash if f.file_hash else f.source_path
        files_dict[key] = entry
    if files_dict:
        node_dict["files"] = files_dict

    if node.children:
        node_dict["children"] = {
            child_name: _build_class_tree(child_node, classifications, folder_decisions)
            for child_name, child_node in sorted(node.children.items())
        }

    return node_dict


def export_tree_json(
    result: TraversalResult,
    out_path: str = TREE_JSON_FILE,
) -> None:
    if result.root_node is None:
        logger.warning("[TREE] root_node is None — skipping tree JSON export")
        return

    tree = _build_class_tree(
        result.root_node,
        result.classifications,
        result.folder_decisions,
    )

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(tree, f, ensure_ascii=False, indent=2)

    print(f"Exported classification tree to {out_path}")


# ====================================================================
#  Execute Moves
# ====================================================================

def execute_moves(
    result: TraversalResult,
    source_dir: str,
    dest_dir: str,
) -> int:
    """
    Copy files from source_dir to dest_dir according to the reorganization plan.

    The original source_dir is left untouched.
    Unmapped (SKIP) files are not copied.
    """
    source_dir = os.path.abspath(source_dir)
    dest_dir = os.path.abspath(dest_dir)

    if dest_dir == source_dir or dest_dir.startswith(source_dir + os.sep):
        raise ValueError(
            f"dest_dir ({dest_dir}) must not be inside source_dir ({source_dir})"
        )

    logger.info(f"[EXEC] Copying {len(result.mappings)} files to {dest_dir}")
    copied = 0
    skipped = 0

    for mapping in result.mappings.values():
        src = os.path.join(source_dir, mapping.source_rel)
        dst = os.path.join(dest_dir, mapping.dest_rel)

        if not os.path.exists(src):
            logger.warning(f"[EXEC] Source not found, skipping: {src}")
            skipped += 1
            continue

        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1

    logger.info(f"[EXEC] Done: {copied} copied, {skipped} skipped")
    print(f"Copied {copied} files -> {dest_dir}  ({skipped} source files missing)")
    return copied


# ====================================================================
#  Task/Sequence Name Pipeline
# ====================================================================

def collect_task_names(mappings: Dict[str, "FileMapping"]) -> Set[str]:
    """Collect the known task-name set from mappings with an identified task."""
    names: Set[str] = set(PRESET_TASK_NAMES)
    for m in mappings.values():
        task_name = normalize_task_name_for_category(m.task_name, m.category)
        if task_name:
            m.task_name = task_name
            m.category_depth = category_depth_for(
                m.source_rel,
                m.task_name,
                m.sequence_name,
                m.category_depth,
            )
            names.add(task_name)
    logger.info(f"[TASK_NAMES] Collected {len(names)} unique task names: {sorted(names)}")
    return names


def rematch_missing_task_names(
    mappings: Dict[str, "FileMapping"],
    known_task_names: Set[str],
    classifier: LLMClassifier,
    classifications: Dict[str, Classification],
) -> None:
    """
    For each mapping with a missing task_name, ask the LLM for a best match
    and accept it only if it matches an already-known task name. Unmatched
    items keep task_name blank.
    """
    missing = [m for m in mappings.values() if not m.task_name]
    if not missing:
        return

    known_list = sorted(known_task_names)
    total = len(missing)
    resolved = 0

    for i, m in enumerate(missing, 1):
        logger.info(f"[TASK_REMATCH] ({i}/{total}) Matching task name for: {m.source_rel}")
        clf = classifications.get(m.source_rel)
        desc = clf.reason if clf else ""
        ancestor_descs = list(clf.ancestor_descriptions) if clf else []

        inferred = classifier.infer_task_name(
            source_path=m.source_rel,
            category=m.category,
            description=desc,
            known_task_names=known_list,
            ancestor_descriptions=ancestor_descs,
        )

        inferred = normalize_task_name_for_category(inferred, m.category)
        if not inferred or inferred not in known_task_names:
            continue

        m.task_name = inferred
        m.category_depth = category_depth_for(
            m.source_rel,
            m.task_name,
            m.sequence_name,
            m.category_depth,
        )
        resolved += 1

    logger.info(
        f"[TASK_REMATCH] Matched {resolved} missing task names "
        f"({total - resolved} left blank)"
    )


def _extract_sequence_from_filename(
    filename: str,
    task_name: Optional[str] = None,
) -> Optional[str]:
    """
    Extract a sequence identifier from a filename.

    Tries (in order):
      1. Numeric components  : "02_disc.pdf"          → "02"
                                "2_23.pdf"            → "2_23"
                                "118_disc.pdf"        → "118"
      2. Numeric suffix      : "boardwork_1_26.pdf"  → "1_26"
      3. Word_Number prefix  : "Discussion_10_CBF.pdf" → "Discussion_10"
      4. Prefix+number stem  : "hw02.pdf", "week03.pdf" → "hw02", "week03"

    Returns None if no recognisable sequence pattern is found.
    """
    stem = os.path.splitext(filename)[0]
    # Split on underscore, hyphen, or space
    parts = re.split(r'[_\-\s]+', stem)
    if not parts:
        return None

    first = parts[0]

    # 1. Numeric prefix, preserving numeric components as written.
    if re.fullmatch(r'\d+', first):
        text_prefix = _filename_text_prefix_after_numeric(parts, task_name)
        numeric_parts = []
        for part in parts:
            if re.fullmatch(r'\d+', part):
                numeric_parts.append(part)
                continue
            break
        digit_seq = "_".join(numeric_parts)
        return f"{text_prefix}_{digit_seq}" if text_prefix else digit_seq

    # 2. Numeric suffix pair (e.g. "boardwork_1_26" -> "1_26")
    if len(parts) >= 3 and re.fullmatch(r'\d{1,2}', parts[-2]) and re.fullmatch(r'\d{1,2}', parts[-1]):
        return f"{parts[-2]}_{parts[-1]}"

    # 3. Word_Number prefix  (e.g. "Discussion" + "10")
    if len(parts) >= 2 and re.fullmatch(r'\d+', parts[1]):
        candidate = f"{first}_{parts[1]}"
        # Don't return something that is just the task_name + number
        # if the word is a vague task abbreviation we want the full form
        return candidate

    # 4. Alphabetic prefix + number in one token (e.g. "hw02", "week03")
    if re.fullmatch(r'[A-Za-z]+\d+[A-Za-z]?', first):
        return first.lower()

    return None


def _filename_text_prefix_after_numeric(
    parts: List[str],
    task_name: Optional[str] = None,
) -> Optional[str]:
    """Return a useful textual prefix after a numeric filename prefix."""
    if len(parts) < 2:
        return None

    candidate = parts[1].strip()
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9]*', candidate):
        return None

    task = (task_name or "").replace("_", "").replace("-", "").lower()
    normalized = candidate.replace("_", "").replace("-", "").lower()
    if task and normalized == task:
        return None
    if task and normalized in TASK_NAME_ALIASES.get(task, set()):
        return None

    return candidate


def _extract_sequence_from_path_components(
    source_rel: str,
    task_name: Optional[str],
) -> Optional[str]:
    """
    Derive a sequence name from folder components in the path.

    Only uses the component that follows the task_name folder.
    No blind fallback to the parent folder name.
    """
    parts = source_rel.replace("\\", "/").split("/")
    if len(parts) < 2 or not task_name:
        return None

    lower_task = task_name.lower()
    for i, p in enumerate(parts[:-1]):          # exclude filename
        if p.lower() == lower_task and i + 1 < len(parts) - 1:
            candidate = parts[i + 1]
            cl = candidate.lower()
            if cl != lower_task:
                return candidate

    return None


def _extract_sequence_from_db(
    filename: str,
    file_index: Dict[str, "FileIndexEntry"],
) -> Optional[str]:
    """
    Scan the DB description / extra_info for sequence markers
    such as "Week 5", "Discussion 10", "Lecture 7", "HW #2".

    Returns a normalised lowercase sequence string, or None.
    """
    entry = file_index.get(filename)
    if not entry:
        return None

    text = " ".join(filter(None, [entry.description, entry.extra_info]))
    if not text.strip():
        return None

    m = re.search(r'\b([A-Za-z]+)\s*#?\s*(\d+[A-Za-z]?)\b', text)
    if m:
        label = re.sub(r'\s+', '', m.group(1)).lower()
        return f"{label}{m.group(2)}"

    m = re.search(r'#\s*(\d+[A-Za-z]?)\b', text)
    if m:
        return m.group(1)

    return None


def is_supported_sequence_name(seq: Optional[str]) -> bool:
    """Return False for unclear fragments like and3D/on3D/tcj3."""
    seq = (seq or "").strip()
    if not seq:
        return False

    normalized = re.sub(r"[^a-z0-9]+", "", seq.lower())
    if not re.search(r"\d", normalized):
        return False

    if re.fullmatch(r"\d+(?:_\d+)*[a-z]?", seq.lower()):
        return True

    typed_patterns = [
        r"discussion[_-]?\d+[a-z]?",
        r"homework[_-]?\d+[a-z]?",
        r"lecture[_-]?\d+[a-z]?",
        r"lec[_-]?\d+[a-z]?",
        r"lab[_-]?\d+[a-z]?",
        r"project[_-]?\d+[a-z]?",
        r"proj[_-]?\d+[a-z]?",
        r"hw[_-]?\d+[a-z]?",
        r"week[_-]?\d+[a-z]?",
        r"chapter[_-]?\d+[a-z]?",
        r"part[_-]?\d+[a-z]?",
        r"spring\d{4}",
        r"fall\d{4}",
    ]
    return any(re.fullmatch(pattern, normalized) for pattern in typed_patterns)


def fill_sequence_names(
    mappings: Dict[str, "FileMapping"],
    file_index: Optional[Dict[str, "FileIndexEntry"]] = None,
    classifier: Optional[LLMClassifier] = None,
    classifications: Optional[Dict[str, Classification]] = None,
    folder_decisions: Optional[Dict[str, ClassificationResult]] = None,
    batch_size: int = 25,
) -> None:
    """
    For each mapping with task_name known and sequence_name missing, attempt to
    fill sequence_name using batch context first, then local fallbacks:

      1. Ask the LLM to infer sequence names across batches of files with the same category/task,
         with context from all other files and folders in that task group.
      2. Extract from the filename  (e.g. "Discussion_10_CBF.pdf" → "Discussion_10")
      3. Extract from path folder components after the task_name folder
      4. Extract from the DB description / extra_info (if file_index is provided)

    Files for which no sequence can be identified keep sequence_name blank
    (their final path will be category/task_name/filename with no seq subfolder).
    """
    all_mappings = list(mappings.values())
    pending = [
        m for m in all_mappings
        if m.task_name and not m.sequence_name
    ]

    if classifier:
        by_task: Dict[Tuple[str, str], Dict[str, list]] = {}
        for m in pending:
            if m.sequence_name or not m.task_name:
                continue
            bucket = by_task.setdefault((m.category, m.task_name), {"files": [], "folders": []})
            bucket["files"].append(m)

        if folder_decisions:
            for path, decision in folder_decisions.items():
                task_name = normalize_task_name_for_category(
                    decision.task_name,
                    decision.category.value,
                )
                decision.task_name = task_name
                if not task_name or decision.sequence_name:
                    continue
                if decision.category == Category.SKIP or decision.is_mixed:
                    continue
                bucket = by_task.setdefault(
                    (decision.category.value, task_name),
                    {"files": [], "folders": []},
                )
                bucket["folders"].append((path, decision))

        for (category, task_name), bucket in sorted(by_task.items()):
            file_mappings = bucket["files"]
            folders = bucket["folders"]
            task_context = _sequence_task_group_context(
                category,
                task_name,
                all_mappings,
                file_index,
                classifications,
                folder_decisions,
            )
            targets = [("file", m) for m in file_mappings] + [
                ("folder", folder_item) for folder_item in folders
            ]
            for start in range(0, len(targets), batch_size):
                batch = targets[start:start + batch_size]
                batch_items = [
                    _sequence_batch_payload(target[1], file_index, classifications)
                    if target[0] == "file"
                    else _sequence_folder_batch_payload(target[1][0], target[1][1])
                    for target in batch
                ]
                batch_context = _limited_sequence_task_context(task_context, batch_items)
                try:
                    decisions = classifier.infer_sequence_names_batch(
                        batch_items,
                        task_context=batch_context,
                    )
                except Exception as e:
                    logger.warning(
                        f"[FILL_SEQ] Batch sequence inference failed for "
                        f"{category}/{task_name} ({len(batch)} items): {e}"
                    )
                    continue

                for target_type, target in batch:
                    source_path = target.source_rel if target_type == "file" else target[0]
                    decision = decisions.get(source_path)
                    if not decision:
                        continue
                    if target_type == "folder":
                        _path, folder_decision = target
                        folder_decision.by_sequence = decision.by_sequence

                    seq = (decision.seq_name or "").strip() or None
                    if seq and not is_supported_sequence_name(seq):
                        logger.debug(
                            f"[FILL_SEQ] rejected unsupported sequence {seq!r} for {source_path}"
                        )
                        seq = None
                    if not seq:
                        continue
                    if target_type == "file":
                        m = target
                        m.sequence_name = seq
                        m.category_depth = category_depth_for(
                            m.source_rel,
                            m.task_name,
                            m.sequence_name,
                            decision.category_depth,
                        )
                        logger.debug(
                            f"[FILL_SEQ] batch {os.path.basename(m.source_rel)} → seq={seq!r}"
                        )
                    else:
                        path, folder_decision = target
                        folder_decision.sequence_name = seq
                        folder_decision.category_depth = category_depth_for(
                            path,
                            folder_decision.task_name,
                            folder_decision.sequence_name,
                            decision.category_depth,
                        )
                        logger.debug(
                            f"[FILL_SEQ] folder {os.path.basename(path)} → seq={seq!r}"
                        )

        inherited = _apply_folder_sequences_to_mappings(pending, folder_decisions)
        if inherited:
            logger.info(
                "[FILL_SEQ] Inherited folder sequence names for %d file mappings",
                inherited,
            )

    for m in pending:
        if m.sequence_name:
            continue

        filename = os.path.basename(m.source_rel)

        # Strategy 1: filename-encoded sequence
        seq = _extract_sequence_from_filename(filename, m.task_name)

        # Strategy 2: path-component sequence
        if not seq:
            seq = _extract_sequence_from_path_components(m.source_rel, m.task_name)

        # Strategy 3: DB description / extra_info
        if not seq and file_index:
            seq = _extract_sequence_from_db(filename, file_index)
        if seq and not is_supported_sequence_name(seq):
            logger.debug(
                f"[FILL_SEQ] rejected unsupported fallback sequence {seq!r} for {filename}"
            )
            seq = None

        if seq:
            m.sequence_name = seq
            m.category_depth = category_depth_for(
                m.source_rel,
                m.task_name,
                m.sequence_name,
                m.category_depth,
            )
            logger.debug(f"[FILL_SEQ] {filename} → seq={seq!r}")

    canonicalized = canonicalize_sequence_names_by_task(
        all_mappings,
        folder_decisions,
    )
    if canonicalized:
        logger.info("[FILL_SEQ] Canonicalized %d equivalent sequence labels", canonicalized)

    filled = sum(1 for m in pending if m.sequence_name)
    still_missing = sum(1 for m in pending if not m.sequence_name)
    logger.info(
        f"[FILL_SEQ] Filled {filled} sequence names; "
        f"{still_missing} remain without a derivable sequence"
    )
    if folder_decisions:
        sequenced_folders = sum(1 for d in folder_decisions.values() if d.sequence_name)
        task_only_folders = sum(
            1 for d in folder_decisions.values() if d.task_name and not d.sequence_name
        )
        logger.info(
            "[FILL_SEQ] Folder decisions: task+seq=%d, task only=%d",
            sequenced_folders,
            task_only_folders,
        )


def _apply_folder_sequences_to_mappings(
    mappings: List["FileMapping"],
    folder_decisions: Optional[Dict[str, ClassificationResult]],
) -> int:
    """Copy a sequence inferred for a folder onto same-task files inside it."""
    if not folder_decisions:
        return 0

    sequence_folders = []
    for folder_path, decision in folder_decisions.items():
        seq = (decision.sequence_name or "").strip()
        task_name = normalize_task_name_for_category(
            decision.task_name,
            decision.category.value,
        )
        if not seq or not task_name:
            continue
        if not is_supported_sequence_name(seq):
            continue
        normalized_path = folder_path.strip("/")
        sequence_folders.append((normalized_path, decision, task_name, seq))

    sequence_folders.sort(key=lambda item: len(item[0]), reverse=True)
    changed = 0

    for mapping in mappings:
        if mapping.sequence_name:
            continue
        mapping_task = normalize_task_name_for_category(mapping.task_name, mapping.category)
        if not mapping_task:
            continue

        source_rel = mapping.source_rel.strip("/")
        for folder_path, decision, folder_task, seq in sequence_folders:
            if mapping.category != decision.category.value:
                continue
            if mapping_task != folder_task:
                continue
            if source_rel != folder_path and not source_rel.startswith(folder_path + "/"):
                continue

            mapping.sequence_name = seq
            mapping.category_depth = category_depth_for(
                mapping.source_rel,
                mapping.task_name,
                mapping.sequence_name,
                decision.category_depth,
            )
            changed += 1
            break

    return changed


def canonicalize_sequence_names_by_task(
    mappings: List["FileMapping"],
    folder_decisions: Optional[Dict[str, ClassificationResult]] = None,
) -> int:
    """Make equivalent same-task sequence labels use the richest shared spelling."""
    candidates: Dict[Tuple[str, str, str], List[Tuple[str, object]]] = {}
    changed = 0

    for mapping in mappings:
        mapping.task_name = normalize_task_name_for_category(
            mapping.task_name,
            mapping.category,
        )
        if mapping.sequence_name and not is_supported_sequence_name(mapping.sequence_name):
            mapping.sequence_name = None
            changed += 1
        key = _sequence_canonical_key(
            mapping.category,
            mapping.task_name,
            mapping.sequence_name,
        )
        if key:
            candidates.setdefault(key, []).append(("file", mapping))

    if folder_decisions:
        for decision in folder_decisions.values():
            decision.task_name = normalize_task_name_for_category(
                decision.task_name,
                decision.category.value,
            )
            if decision.sequence_name and not is_supported_sequence_name(decision.sequence_name):
                decision.sequence_name = None
                changed += 1
            key = _sequence_canonical_key(
                decision.category.value,
                decision.task_name,
                decision.sequence_name,
            )
            if key:
                candidates.setdefault(key, []).append(("folder", decision))

    for items in candidates.values():
        if len(items) < 2:
            continue
        canonical = _choose_canonical_sequence_label(
            item.sequence_name
            for _, item in items
            if getattr(item, "sequence_name", None)
        )
        if not canonical:
            continue

        for item_type, item in items:
            if item.sequence_name == canonical:
                continue
            item.sequence_name = canonical
            changed += 1

    return changed


def _sequence_canonical_key(
    category: str,
    task_name: Optional[str],
    sequence_name: Optional[str],
) -> Optional[Tuple[str, str, str]]:
    """Group sequence aliases like hw3 and Homework_3 by task plus number marker."""
    task = normalize_task_name(task_name)
    seq = (sequence_name or "").strip()
    if not category or not task or not seq:
        return None

    normalized = re.sub(r"[^a-z0-9]+", "", seq.lower())
    numbers = re.findall(r"\d+[a-z]?", normalized)
    if not numbers:
        return None

    return (category, task, numbers[-1])


def _choose_canonical_sequence_label(labels) -> Optional[str]:
    """Prefer the most descriptive sequence label while preserving original spelling."""
    labels = [label for label in labels if label]
    if not labels:
        return None

    def _score(label: str) -> tuple:
        alpha_count = sum(ch.isalpha() for ch in label)
        separator_count = sum(ch in "_- " for ch in label)
        return (alpha_count > 2, separator_count > 0, len(label))

    return max(labels, key=_score)


def _sequence_task_group_context(
    category: str,
    task_name: str,
    mappings: List["FileMapping"],
    file_index: Optional[Dict[str, "FileIndexEntry"]] = None,
    classifications: Optional[Dict[str, Classification]] = None,
    folder_decisions: Optional[Dict[str, ClassificationResult]] = None,
) -> List[Dict[str, str]]:
    """Build sequence context from files and folders sharing category/task."""
    context: List[Dict[str, str]] = []
    normalized_task = normalize_task_name(task_name)

    for mapping in sorted(mappings, key=lambda m: m.source_rel):
        if mapping.category != category:
            continue
        if normalize_task_name_for_category(mapping.task_name, mapping.category) != normalized_task:
            continue

        payload = _sequence_batch_payload(mapping, file_index, classifications)
        context.append({
            "type": "file",
            "path": payload["source_path"],
            "name": payload["file_name"],
            "parent_folder": payload["parent_folder"],
            "sequence_name": mapping.sequence_name or "",
            "category_depth": str(mapping.category_depth),
            "description": compact_text(
                payload["description"],
                MAX_SEQUENCE_DESCRIPTION_CHARS,
            ),
        })

    if folder_decisions:
        for path, decision in sorted(folder_decisions.items()):
            if decision.category.value != category:
                continue
            if normalize_task_name_for_category(decision.task_name, decision.category.value) != normalized_task:
                continue

            description_parts = [
                decision.folder_description or "",
                decision.reason or "",
            ]
            context.append({
                "type": "folder",
                "path": path,
                "name": os.path.basename(path),
                "parent_folder": os.path.dirname(path),
                "sequence_name": decision.sequence_name or "",
                "category_depth": str(decision.category_depth),
                "description": compact_text(
                    " ".join(p for p in description_parts if p),
                    MAX_SEQUENCE_DESCRIPTION_CHARS,
                ),
            })

    return context


def _limited_sequence_task_context(
    task_context: List[Dict[str, str]],
    batch_items: List[Dict[str, str]],
    max_items: int = MAX_SEQUENCE_CONTEXT_ITEMS,
) -> List[Dict[str, str]]:
    """Keep the most useful same-task context while bounding prompt size."""
    if len(task_context) <= max_items:
        return task_context

    batch_paths = {item.get("source_path", "") for item in batch_items}
    batch_parents = {item.get("parent_folder", "") for item in batch_items}

    def _context_rank(item: Dict[str, str]) -> tuple:
        path = item.get("path", "")
        parent = item.get("parent_folder", "")
        has_sequence = bool(item.get("sequence_name"))
        if path in batch_paths:
            rank = 0
        elif has_sequence:
            rank = 1
        elif parent in batch_parents:
            rank = 2
        else:
            rank = 3
        return (rank, path)

    return sorted(task_context, key=_context_rank)[:max_items]


def _sequence_batch_payload(
    mapping: "FileMapping",
    file_index: Optional[Dict[str, "FileIndexEntry"]] = None,
    classifications: Optional[Dict[str, Classification]] = None,
) -> Dict[str, str]:
    """Build one compact item for batch sequence-name inference."""
    filename = os.path.basename(mapping.source_rel)
    parent_folder = os.path.dirname(mapping.source_rel)
    entry = file_index.get(filename) if file_index else None
    clf = classifications.get(mapping.source_rel) if classifications else None

    description_parts: List[str] = []
    if entry and entry.description:
        description_parts.append(entry.description)
    if entry and entry.extra_info:
        description_parts.append(entry.extra_info)
    if clf and clf.reason:
        description_parts.append(clf.reason)

    return {
        "item_type": "file",
        "source_path": mapping.source_rel,
        "file_name": filename,
        "parent_folder": parent_folder,
        "category": mapping.category,
        "task_name": mapping.task_name or "",
        "description": compact_text(
            " ".join(description_parts),
            MAX_SEQUENCE_ITEM_DESCRIPTION_CHARS,
        ),
    }


def _sequence_folder_batch_payload(
    path: str,
    decision: ClassificationResult,
) -> Dict[str, str]:
    """Build one compact folder item for batch sequence-name inference."""
    description_parts = [
        decision.folder_description or "",
        decision.reason or "",
    ]
    return {
        "item_type": "folder",
        "source_path": path,
        "file_name": os.path.basename(path),
        "parent_folder": os.path.dirname(path),
        "category": decision.category.value,
        "task_name": decision.task_name or "",
        "description": compact_text(
            " ".join(p for p in description_parts if p),
            MAX_SEQUENCE_ITEM_DESCRIPTION_CHARS,
        ),
    }


def _derive_sequence_from_path(source_rel: str, task_name: Optional[str]) -> Optional[str]:
    """
    Legacy wrapper — delegates to the new helpers.
    Kept for backward compatibility with any external callers.
    """
    filename = os.path.basename(source_rel)
    return (
        _extract_sequence_from_filename(filename, task_name)
        or _extract_sequence_from_path_components(source_rel, task_name)
    )


def build_final_path(m: "FileMapping") -> str:
    """
    Build the structured final destination path:
      {category}/{task_name}/{sequence_name}/{source suffix deeper than category_depth}
    Falls back gracefully when task_name, sequence_name, or deeper suffix is absent.
    """
    return build_dest_rel(
        m.category,
        m.source_rel,
        m.task_name,
        m.sequence_name,
        m.category_depth,
    )


def flatten_single_file_folders(paths_by_source: Dict[str, str]) -> Dict[str, str]:
    """
    Re-check final paths and flatten one-file folders until stable.

    If a destination folder directly contains exactly one file, that file is moved
    to the folder's parent. Collisions are left unchanged. The top-level category
    folder is preserved.
    """
    flattened = dict(paths_by_source)
    max_passes = max(
        (len(dest.replace("\\", "/").split("/")) for dest in flattened.values()),
        default=0,
    )

    for _ in range(max_passes):
        files_by_parent: Dict[str, List[str]] = {}
        child_folders_by_parent: Dict[str, Set[str]] = {}

        for source, dest in flattened.items():
            parts = [p for p in dest.replace("\\", "/").split("/") if p]
            parent = "/".join(parts[:-1])
            files_by_parent.setdefault(parent, []).append(source)

            for depth in range(1, len(parts) - 1):
                folder = "/".join(parts[:depth])
                child = "/".join(parts[:depth + 1])
                child_folders_by_parent.setdefault(folder, set()).add(child)

        occupied = set(flattened.values())
        next_paths: Dict[str, str] = {}
        changed = False

        for source, dest in flattened.items():
            parent = os.path.dirname(dest)
            filename = os.path.basename(dest)
            parent_files = files_by_parent.get(parent, [])
            parent_child_folders = child_folders_by_parent.get(parent, set())

            if (
                parent
                and "/" in parent
                and len(parent_files) == 1
                and not parent_child_folders
            ):
                grandparent = os.path.dirname(parent)
                candidate = _join_rel(grandparent, filename)
                if candidate not in occupied or candidate == dest:
                    next_paths[source] = candidate
                    occupied.discard(dest)
                    occupied.add(candidate)
                    changed = True
                    continue

                logger.warning(
                    "[FLATTEN] Skipping %s -> %s because destination already exists",
                    dest,
                    candidate,
                )

            next_paths[source] = dest

        flattened = next_paths
        if not changed:
            break

    return flattened


def apply_flattened_final_paths(mappings: Dict[str, "FileMapping"]) -> None:
    """Build final paths, flatten one-file folders, and store them in mappings."""
    final_paths = {
        source: build_final_path(mapping)
        for source, mapping in mappings.items()
    }
    flattened_paths = flatten_single_file_folders(final_paths)

    changed = 0
    for source, final_path in flattened_paths.items():
        mapping = mappings[source]
        if mapping.dest_rel != final_path:
            changed += 1
        mapping.dest_rel = final_path

    logger.info("[FLATTEN] Updated %d destination paths", changed)


FINAL_PATHS_JSON_FILE = "bfs_v4_final_paths.json"


def export_final_paths_json(
    result: "TraversalResult",
    known_task_names: Set[str],
    out_path: str = FINAL_PATHS_JSON_FILE,
) -> None:
    """
    Export the final structured paths to JSON.
    Each entry includes: source, category, task_name, sequence_name,
    category_depth, final_path.
    """

    def _mapping_dict(m: FileMapping) -> dict:
        m.category_depth = category_depth_for(
            m.source_rel,
            m.task_name,
            m.sequence_name,
            m.category_depth,
        )
        final_path = (
            m.dest_rel
            if os.path.basename(m.dest_rel) == os.path.basename(m.source_rel)
            else build_final_path(m)
        )
        return {
            "source": m.source_rel,
            "category": m.category,
            "task_name": m.task_name,
            "sequence_name": m.sequence_name,
            "category_depth": m.category_depth,
            "final_path": final_path,
        }

    sorted_mappings = sorted(result.mappings.values(), key=lambda x: x.source_rel)
    all_items = [_mapping_dict(m) for m in sorted_mappings]
    task_and_sequence = sum(1 for m in result.mappings.values() if m.task_name and m.sequence_name)
    task_only = sum(1 for m in result.mappings.values() if m.task_name and not m.sequence_name)
    no_task = sum(1 for m in result.mappings.values() if not m.task_name)

    payload = {
        "metadata": {
            "total_files": len(result.mappings),
            "task_and_sequence": task_and_sequence,
            "task_only": task_only,
            "no_task": no_task,
            "known_task_names": sorted(known_task_names),
        },
        "all_final_paths": all_items,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"Exported final paths to {out_path} ({len(all_items)} entries)")


# ====================================================================
#  Markdown Report Generator
# ====================================================================

def _build_dest_tree(result: TraversalResult) -> dict:
    tree: dict = {}
    for m in result.mappings.values():
        parts = m.dest_rel.split("/")
        cur = tree
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur.setdefault("__files__", []).append(parts[-1])
    return tree


def _count_tree_files(node: dict) -> int:
    count = len(node.get("__files__", []))
    for k, v in node.items():
        if k != "__files__":
            count += _count_tree_files(v)
    return count


def _render_dest_tree(node: dict, lines: List[str], indent: int, max_depth: int = 4) -> None:
    pad = "  " * indent

    for key in sorted(k for k in node if k != "__files__"):
        child = node[key]
        n_files = _count_tree_files(child)
        lines.append(f"{pad}{key}/  ({n_files} files)")
        if indent < max_depth:
            _render_dest_tree(child, lines, indent + 1, max_depth)

    files = node.get("__files__", [])
    if files and indent >= max_depth:
        lines.append(f"{pad}  ... {len(files)} files")
    elif files and len(files) <= 10:
        for fn in sorted(files):
            lines.append(f"{pad}  {fn}")
    elif files:
        for fn in sorted(files)[:5]:
            lines.append(f"{pad}  {fn}")
        lines.append(f"{pad}  ... and {len(files) - 5} more")


def generate_report(
    result: TraversalResult,
    classifier: LLMClassifier,
    out_path: str = REPORT_MD_FILE,
) -> None:
    lines: List[str] = []

    lines.append("# BFS v4 — Reorganization Report\n")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    lines.append("## 1. Summary\n")
    cat_counts: Dict[str, int] = {}
    for c in result.classifications.values():
        cat_counts[c.category.value] = cat_counts.get(c.category.value, 0) + 1

    lines.append("| Metric | Value |")
    lines.append("|--------|------:|")
    lines.append(f"| Files on disk | {result.files_on_disk_count} |")
    for cat in ["study", "practice", "support", "skip"]:
        lines.append(f"| Classified as **{cat}** | {cat_counts.get(cat, 0)} |")
    lines.append(f"| Files classified via folder | {result.files_classified_via_folder} |")
    lines.append(f"| Files classified individually | {result.files_classified_individually} |")
    lines.append(f"| Total file mappings | {len(result.mappings)} |")
    lines.append(f"| Skipped folders (still descended) | {len(result.skipped_folders)} |")
    lines.append(f"| Files not in DB | {len(result.files_missing_in_db)} |")
    lines.append(f"| Stale DB entries (no file) | {len(result.files_stale_in_db)} |")
    lines.append(f"| LLM calls | {len(classifier.debug_log)} |")
    lines.append("")

    lines.append("## 2. Folder Decisions\n")
    lines.append("| Folder | Category | Mixed | Reason | Description |")
    lines.append("|--------|----------|:-----:|--------|-------------|")
    for path, dec in sorted(result.folder_decisions.items()):
        desc_short = (dec.folder_description or "")[:100]
        reason_short = (dec.reason or "")[:120]
        mixed = "Y" if dec.is_mixed else ""
        lines.append(
            f"| `{path}` | {dec.category.value} | {mixed} | {reason_short} | {desc_short} |"
        )
    lines.append("")

    lines.append("## 3. Destination Tree\n")
    dest_tree = _build_dest_tree(result)
    lines.append("```")
    _render_dest_tree(dest_tree, lines, indent=0)
    lines.append("```")
    lines.append("")

    if result.skipped_folders:
        lines.append("## 4. Skipped Folders (descended, not pruned)\n")
        for folder in sorted(result.skipped_folders):
            dec = result.folder_decisions.get(folder)
            reason = (dec.reason[:300] + "...") if dec and len(dec.reason) > 300 else (dec.reason if dec else "")
            lines.append(f"- `{folder}` — {reason}")
        lines.append("")

    if result.files_missing_in_db:
        lines.append("## 5. Files Not Found in DB\n")
        lines.append(f"{len(result.files_missing_in_db)} files on disk have no matching DB entry ")
        lines.append("(classified without descriptions):\n")
        shown = result.files_missing_in_db[:30]
        for fn in shown:
            lines.append(f"- `{fn}`")
        if len(result.files_missing_in_db) > 30:
            lines.append(f"- ... and {len(result.files_missing_in_db) - 30} more")
        lines.append("")

    if result.files_stale_in_db:
        lines.append("## 6. Stale DB Entries\n")
        lines.append(f"{len(result.files_stale_in_db)} DB entries have no corresponding file on disk:\n")
        shown = result.files_stale_in_db[:30]
        for fn in shown:
            lines.append(f"- `{fn}`")
        if len(result.files_stale_in_db) > 30:
            lines.append(f"- ... and {len(result.files_stale_in_db) - 30} more")
        lines.append("")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"Report saved to {out_path} ({len(lines)} lines)")


# ====================================================================
#  Convenience Wrappers
# ====================================================================

def _tree_to_serializable(node: dict) -> dict:
    out: dict = {}
    files = node.get("__files__", [])
    if files:
        out["_files"] = sorted(files)
    for key in sorted(k for k in node if k != "__files__"):
        out[key] = _tree_to_serializable(node[key])
    return out


def export_mappings_json(result: TraversalResult, out_path: str = PLAN_JSON_FILE) -> None:
    dest_tree = _build_dest_tree(result)

    payload = {
        "stats": {
            "files_on_disk": result.files_on_disk_count,
            "files_via_folder": result.files_classified_via_folder,
            "files_individual": result.files_classified_individually,
            "total_mappings": len(result.mappings),
            "skipped_folders": len(result.skipped_folders),
            "files_missing_in_db": len(result.files_missing_in_db),
            "files_stale_in_db": len(result.files_stale_in_db),
        },
        "folder_decisions": {
            path: {
                "category": dec.category.value,
                "is_mixed": dec.is_mixed,
                "folder_description": dec.folder_description,
                "by_type": dec.by_type,
                "by_sequence": dec.by_sequence,
                "task_name": dec.task_name,
                "sequence_name": dec.sequence_name,
                "category_depth": dec.category_depth,
            }
            for path, dec in result.folder_decisions.items()
        },
        "dest_tree": _tree_to_serializable(dest_tree),
        "mappings": [
            {
                "source": m.source_rel,
                "dest": m.dest_rel,
                "category": m.category,
                "task_name": m.task_name,
                "sequence_name": m.sequence_name,
                "category_depth": m.category_depth,
            }
            for m in result.mappings.values()
        ],
        "skipped_folders": result.skipped_folders,
        "files_missing_in_db": result.files_missing_in_db,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"Exported plan to {out_path}")


def bfs_reorganize(
    course_root: str,
    db_path: str = DEFAULT_DB_PATH,
    model: str = "gpt-5-mini-2025-08-07",
    report_path: str = REPORT_MD_FILE,
    json_path: str = PLAN_JSON_FILE,
    tree_path: str = TREE_JSON_FILE,
    final_paths_path: str = FINAL_PATHS_JSON_FILE,
    dest_dir: Optional[str] = None,
) -> TraversalResult:
    db = CourseDB(db_path)
    db.connect()

    try:
        classifier = LLMClassifier(model=model)
        traverser = BFSTraverser(db, classifier)
        result = traverser.traverse(course_root)

        # --- Post-BFS task/sequence name pipeline ---
        known_task_names = collect_task_names(result.mappings)
        rematch_missing_task_names(
            result.mappings, known_task_names, classifier, result.classifications
        )

        # Infer sequence names from batches of files with the same task.
        file_index = db.load_file_index()
        fill_sequence_names(
            result.mappings,
            file_index,
            classifier=classifier,
            classifications=result.classifications,
            folder_decisions=result.folder_decisions,
        )
        apply_flattened_final_paths(result.mappings)

        # --- Exports ---
        generate_report(result, classifier, report_path)
        export_mappings_json(result, json_path)
        export_tree_json(result, tree_path)
        export_final_paths_json(result, known_task_names, final_paths_path)

        if dest_dir:
            execute_moves(result, course_root, dest_dir)
    finally:
        db.close()

    return result


def print_classification_summary(result: TraversalResult) -> None:
    print("\n" + "=" * 70)
    print("BFS v4 — CLASSIFICATION SUMMARY")
    print("=" * 70)

    cat_counts: Dict[str, int] = {}
    for c in result.classifications.values():
        cat_counts[c.category.value] = cat_counts.get(c.category.value, 0) + 1

    print("\nBy Category:")
    for cat in ["study", "practice", "support", "skip"]:
        print(f"  {cat:10s}: {cat_counts.get(cat, 0)}")

    print(f"\nClassification Method:")
    print(f"  Via folder:     {result.files_classified_via_folder}")
    print(f"  Individually:   {result.files_classified_individually}")

    print(f"\nSkipped Folders ({len(result.skipped_folders)}) — all descended:")
    for folder in result.skipped_folders:
        print(f"  - {folder}")

    print(f"\nFile Mappings: {len(result.mappings)}")
    print("=" * 70)


# ====================================================================
#  CLI Entry Point
# ====================================================================

def main():
    import argparse
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="BFS v4 — Course File Reorganization Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Classify only (no file copies):\n"
            "  python bfs_v4.py --source \"./EECS 106B_meng\" --db ./file.db\n\n"
            "  # Classify + copy into a new folder (original untouched):\n"
            "  python bfs_v4.py --source \"./EECS 106B_meng\" --db ./file.db "
            "--execute --dest ./106B_reorganized\n"
        ),
    )
    parser.add_argument("--source", "-s", required=True, help="Course root directory")
    parser.add_argument(
        "--db", "-d", default=DEFAULT_DB_PATH,
        help=f"SQLite database path (default: {DEFAULT_DB_PATH})"
    )
    parser.add_argument("--model", default="gpt-5-mini-2025-08-07", help="OpenAI model")
    parser.add_argument("--json-out", default=PLAN_JSON_FILE, help="Plan JSON output file")
    parser.add_argument("--tree-out", default=TREE_JSON_FILE, help="Tree JSON output file for evaluation")
    parser.add_argument("--report", default=REPORT_MD_FILE, help="Markdown report output file")
    parser.add_argument(
        "--final-paths", default=FINAL_PATHS_JSON_FILE,
        help=f"Final structured paths JSON (default: {FINAL_PATHS_JSON_FILE})"
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="Actually copy files to --dest (original source is kept intact)"
    )
    parser.add_argument(
        "--dest",
        help="Destination directory for reorganized files (required when --execute is set)"
    )
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()

    source = os.path.abspath(args.source)
    if not os.path.isdir(source):
        print(f"Error: directory not found: {source}")
        sys.exit(1)

    if args.execute and not args.dest:
        print("Error: --dest is required when --execute is set")
        sys.exit(1)

    if not os.environ.get("OPENAI_API_KEY"):
        print("Error: OPENAI_API_KEY not set")
        sys.exit(1)

    print("=" * 70)
    print("BFS v4 — Course File Reorganization Agent")
    print("=" * 70)
    print(f"Source:    {source}")
    print(f"DB:        {args.db}")
    print(f"Model:     {args.model}")
    print(f"Plan:      {args.json_out}")
    print(f"Tree:      {args.tree_out}")
    print(f"Report:    {args.report}")
    print(f"FinalPaths:{args.final_paths}")
    if args.execute:
        print(f"Execute:   YES  ->  {args.dest}")
    else:
        print("Execute:   NO (dry-run — use --execute --dest <dir> to copy files)")
    print("=" * 70)

    result = bfs_reorganize(
        course_root=source,
        db_path=args.db,
        model=args.model,
        report_path=args.report,
        json_path=args.json_out,
        tree_path=args.tree_out,
        final_paths_path=args.final_paths,
        dest_dir=args.dest if args.execute else None,
    )

    print_classification_summary(result)

    if args.verbose:
        print("\nMappings:")
        print("-" * 70)
        for _, m in sorted(result.mappings.items()):
            print(f"  {m.source_rel} -> {m.dest_rel}")


if __name__ == "__main__":
    main()
