#!/usr/bin/env python3
"""
bfs_v2.py

BFS-based File Reorganization Agent with LLM Classification and DB Integration.

Pipeline:
  1. Connect to SQLite DB (file.db) and load the complete file index
  2. Scan the course directory and build a folder tree
  3. BFS traversal from root, classifying folders top-down via classify_v1
  4. When confident at folder level -> assign category to all contents
  5. When low confidence or mixed -> descend to classify individual files
  6. Produce file move mappings and a Markdown report

Fixes implemented:
  - FIX #1: Confident skip prunes subtree entirely
  - FIX #2: folder_description written to DB only on confident assignment
  - FIX #3: Combined reason + decide in 1 LLM call (in classify_v1)
  - FIX #4: Visited set prevents duplicate processing
  - FIX #5: top_level_folder() for file-level dest path
  - FIX #6: Sync warnings between DB and disk
  - FIX #7: Mappings keyed by source_rel (no duplicates)

Output:
  - bfs_v2_plan.json          — full reorganization plan
  - bfs_v2_report.md          — human-readable report with LLM I/O and organized tree
  - bfs_v2_llm_debug.json     — raw LLM debug log
"""

import json
import logging
import os
import sqlite3
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Set, Union

# Import classifier and shared types from classify_v1
from classify_v1 import (
    LLMClassifier,
    Category,
    FileMeta,
    FolderNode,
    FileIndexEntry,
    FolderStats,
    ClassificationResult,
    DEFAULT_CONFIDENCE_THRESHOLD,
    collect_all_files,
    MAX_CONCAT_DESC_CHARS,
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

MAX_ANCESTOR_DEPTH = 10
DEFAULT_DB_PATH = "file.db"
LLM_DEBUG_LOG_FILE = "bfs_v2_llm_debug.json"
REPORT_MD_FILE = "bfs_v2_report.md"
PLAN_JSON_FILE = "bfs_v2_plan.json"


# ====================================================================
#  Additional Data Structures (BFS-specific)
# ====================================================================

@dataclass
class Classification:
    """Final classification record."""
    path: str
    category: Category
    confidence: float
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


@dataclass
class TraversalResult:
    """Complete output of the BFS pipeline."""
    classifications: Dict[str, Classification]
    folder_decisions: Dict[str, ClassificationResult]
    skipped_folders: List[str]
    mappings: Dict[str, FileMapping]   # FIX #7: keyed by source_rel
    files_classified_individually: int
    files_classified_via_folder: int


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
        """Open SQLite connection with validation."""
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"Database not found: {self.db_path}")

        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

        # Validate schema
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
            "SELECT uuid, file_name, description, original_path, "
            "relative_path, extra_info FROM file"
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
                original_path=r["original_path"],
                relative_path=r["relative_path"],
                extra_info=r["extra_info"],
            )
            if fname not in index:
                index[fname] = entry

        logger.info(f"[DB] Loaded {len(index)} file entries")
        return index

    def get_uuids_for_files(self, file_names: List[str]) -> List[str]:
        """Look up UUIDs for a list of file names."""
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

        Handles extra_info that is:
          - NULL / empty  -> create new dict
          - a JSON object -> merge into it
          - a JSON array (e.g. video transcripts) -> wrap in {"_original": ..., "folder_description": ...}
          - unparseable   -> overwrite with new dict

        FIX #2: Only called on confident folder-level assignment.
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

            # Build the new extra_info dict
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
                    # Preserve the original list under _original
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

        node.files.append(FileMeta(
            source_path=rel_path,
            folder_path=folder,
            file_name=fname,
            description=desc,
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
    max_chars: int = MAX_CONCAT_DESC_CHARS,
) -> str:
    """Build a concatenated description string, truncated to max_chars."""
    parts: List[str] = []
    total = 0

    for f in files:
        entry = file_index.get(f.file_name)
        desc = ""
        if entry and entry.description:
            desc = entry.description.replace("\n", " ").strip()
        elif f.description:
            desc = f.description.replace("\n", " ").strip()
        if not desc:
            continue

        line = f"{f.file_name}: {desc}"
        if total + len(line) > max_chars:
            remaining = max_chars - total
            if remaining > 20:
                parts.append(line[:remaining] + "...")
            parts.append(f"[truncated — {len(files) - len(parts)} more files]")
            break
        parts.append(line)
        total += len(line) + 1

    return "\n".join(parts)


def top_level_folder(source_path: str) -> str:
    """Extract top-level folder from a relative path. FIX #5."""
    parts = source_path.replace("\\", "/").split("/")
    return parts[0] if len(parts) > 1 else ""


def build_dest_rel(category: str, top_folder: str, tail: str) -> str:
    """Build destination relative path from category + top_folder + tail."""
    if category == "practice":
        return _join_rel("practice", top_folder, tail)
    if category == "support":
        return _join_rel("support", top_folder, tail)
    if category == "study":
        if top_folder == "lecture":
            return _join_rel("study", "lecture", tail)
        return _join_rel("study", "lecture", top_folder, tail)
    return _join_rel(top_folder, tail)


def _join_rel(*parts: str) -> str:
    clean = [p.strip().replace("\\", "/") for p in parts if p and p.strip()]
    return "/".join(clean)


def log_sync_warnings(
    file_index: Dict[str, FileIndexEntry],
    files_on_disk: List[str],
) -> None:
    """FIX #6: Log warnings for DB/disk mismatches."""
    disk_filenames = {os.path.basename(f) for f in files_on_disk}
    db_filenames = set(file_index.keys())

    missing_in_db = disk_filenames - db_filenames
    stale_in_db = db_filenames - disk_filenames

    if missing_in_db:
        logger.warning(
            f"[SYNC] {len(missing_in_db)} files on disk have NO DB entry. "
            f"Examples: {sorted(missing_in_db)[:5]}"
        )
    if stale_in_db:
        logger.warning(
            f"[SYNC] {len(stale_in_db)} DB entries have no file on disk. "
            f"Examples: {sorted(stale_in_db)[:5]}"
        )
    if not missing_in_db and not stale_in_db:
        logger.info("[SYNC] DB and disk are fully in sync.")


# ====================================================================
#  BFS Traverser
# ====================================================================

class BFSTraverser:
    """Core BFS traversal engine implementing the t2c2spr pipeline."""

    def __init__(
        self,
        db: CourseDB,
        classifier: LLMClassifier,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ):
        self.db = db
        self.classifier = classifier
        self.confidence_threshold = confidence_threshold

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

        log_sync_warnings(file_index, files_on_disk)

        root = build_tree(source_path, files_on_disk, file_index)
        logger.info(f"[BFS] Tree: {len(root.children)} top-level folders")

        result = self._bfs_classify(root, file_index)

        logger.info(
            f"[BFS] Done: {result.files_classified_via_folder} via folder, "
            f"{result.files_classified_individually} individually, "
            f"{len(result.skipped_folders)} skipped, "
            f"{len(result.mappings)} mappings"
        )

        self.classifier.save_debug_log()
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
        ancestor_desc_map: Dict[str, List[str]] = {}
        task_queue: deque[Union[FolderNode, FileMeta]] = deque()

        for child in root.children.values():
            task_queue.append(child)
            ancestor_desc_map[child.path] = []
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
                    item, file_index, ancestor_desc_map, task_queue, seen,
                    classifications, folder_decisions, skipped_folders, mappings,
                )
            elif isinstance(item, FileMeta):
                self._process_file(
                    item, file_index, ancestor_desc_map,
                    classifications, mappings,
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
        task_queue: deque,
        _seen: Set[str],
        classifications: Dict[str, Classification],
        folder_decisions: Dict[str, ClassificationResult],
        skipped_folders: List[str],
        mappings: Dict[str, FileMapping],
    ) -> None:
        my_ancestors = ancestor_desc_map.get(item.path, [])

        folder_files = collect_all_files(item)
        folder_stats = compute_folder_stats(item)
        concat_desc = build_concat_desc(folder_files, file_index)

        result = self.classifier.classify_folder(
            item, file_index, folder_stats, concat_desc,
            ancestor_descriptions=my_ancestors,
        )
        folder_decisions[item.path] = result

        logger.info(
            f"[BFS] Folder '{item.path}': {result.category.value} "
            f"(conf={result.confidence:.2f}, mixed={result.is_mixed})"
        )

        child_ancestors = list(my_ancestors)
        if result.folder_description and len(child_ancestors) < MAX_ANCESTOR_DEPTH:
            child_ancestors.append(result.folder_description)

        # FIX #1: Confident skip -> prune subtree
        if (
            result.category == Category.SKIP
            and result.confidence >= self.confidence_threshold
        ):
            skipped_folders.append(item.path)
            classifications[item.path] = Classification(
                path=item.path,
                category=Category.SKIP,
                confidence=result.confidence,
                reason=result.reason,
                classified_at_level="folder",
                ancestor_descriptions=list(my_ancestors),
            )
            logger.info(f"[BFS]   -> Confident SKIP, pruning subtree")
            return

        should_descend = (
            not result.is_confident(self.confidence_threshold)
            or result.is_mixed
        )

        if result.category == Category.SKIP:
            skipped_folders.append(item.path)
            should_descend = True

        descent_note = " [descended]" if should_descend else ""
        classifications[item.path] = Classification(
            path=item.path,
            category=result.category,
            confidence=result.confidence,
            reason=result.reason + descent_note,
            classified_at_level="folder",
            ancestor_descriptions=list(my_ancestors),
        )

        if should_descend:
            logger.info(f"[BFS]   -> Descending into '{item.path}'")
            for child in item.children.values():
                ancestor_desc_map[child.path] = child_ancestors
                task_queue.append(child)
            ancestor_desc_map[item.path] = child_ancestors
            for f in item.files:
                task_queue.append(f)
        else:
            logger.info(f"[BFS]   -> Confident, assigning {len(folder_files)} files")

            # FIX #2: Write to DB only on confident assignment
            if result.folder_description:
                file_names = [f.file_name for f in folder_files]
                uuids = self.db.get_uuids_for_files(file_names)
                if uuids:
                    self.db.update_folder_description_bulk(uuids, result.folder_description)

            for f in folder_files:
                classifications[f.source_path] = Classification(
                    path=f.source_path,
                    category=result.category,
                    confidence=result.confidence,
                    reason=f"Inherited from folder '{item.path}': {result.reason}",
                    classified_at_level="folder",
                    parent_folder=item.path,
                    ancestor_descriptions=list(child_ancestors),
                )

                top = top_level_folder(f.source_path)
                tail = f.source_path
                if top:
                    tail = f.source_path[len(top):].lstrip("/")
                dest_rel = build_dest_rel(result.category.value, top, tail)

                mappings[f.source_path] = FileMapping(
                    source_rel=f.source_path,
                    dest_rel=dest_rel,
                    top_folder=top,
                    category=result.category.value,
                    reason=result.reason,
                )

    def _process_file(
        self,
        item: FileMeta,
        file_index: Dict[str, FileIndexEntry],
        ancestor_desc_map: Dict[str, List[str]],
        classifications: Dict[str, Classification],
        mappings: Dict[str, FileMapping],
    ) -> None:
        file_ancestors = ancestor_desc_map.get(item.folder_path, [])

        result = self.classifier.classify_file(
            item, file_index,
            ancestor_descriptions=file_ancestors,
        )

        logger.info(
            f"[BFS] File '{item.source_path}': {result.category.value} "
            f"(conf={result.confidence:.2f})"
        )

        if result.category == Category.SKIP:
            return

        classifications[item.source_path] = Classification(
            path=item.source_path,
            category=result.category,
            confidence=result.confidence,
            reason=result.reason,
            classified_at_level="file",
            ancestor_descriptions=list(file_ancestors),
        )

        top = top_level_folder(item.source_path)
        tail = item.source_path
        if top:
            tail = item.source_path[len(top):].lstrip("/")
        dest_rel = build_dest_rel(result.category.value, top, tail)

        mappings[item.source_path] = FileMapping(
            source_rel=item.source_path,
            dest_rel=dest_rel,
            top_folder=top,
            category=result.category.value,
            reason=result.reason,
        )


# ====================================================================
#  Markdown Report Generator
# ====================================================================

def generate_report(
    result: TraversalResult,
    classifier: LLMClassifier,
    out_path: str = REPORT_MD_FILE,
) -> None:
    """
    Generate a comprehensive Markdown report including:
      1. Summary statistics
      2. Folder decisions table (with LLM reasoning)
      3. LLM input/output log for each classification call
      4. Organized destination tree
      5. Full file mapping table
    """
    lines: List[str] = []

    # --- Header ---
    lines.append("# BFS v2 — Reorganization Report\n")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # --- 1. Summary ---
    lines.append("## 1. Summary\n")
    cat_counts: Dict[str, int] = {}
    for c in result.classifications.values():
        cat_counts[c.category.value] = cat_counts.get(c.category.value, 0) + 1

    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    for cat in ["study", "practice", "support", "skip"]:
        lines.append(f"| {cat} items | {cat_counts.get(cat, 0)} |")
    lines.append(f"| Files via folder | {result.files_classified_via_folder} |")
    lines.append(f"| Files individually | {result.files_classified_individually} |")
    lines.append(f"| Skipped folders | {len(result.skipped_folders)} |")
    lines.append(f"| Total mappings | {len(result.mappings)} |")
    lines.append("")

    # --- 2. Folder Decisions ---
    lines.append("## 2. Folder Decisions\n")
    lines.append("| Folder | Category | Confidence | Mixed | Description |")
    lines.append("|--------|----------|------------|-------|-------------|")
    for path, dec in sorted(result.folder_decisions.items()):
        desc_short = (dec.folder_description or "—")[:60]
        lines.append(
            f"| `{path}` | **{dec.category.value}** | {dec.confidence:.2f} "
            f"| {'yes' if dec.is_mixed else 'no'} | {desc_short} |"
        )
    lines.append("")

    # --- 3. Folder Reasoning Details ---
    lines.append("## 3. Folder Classification Details\n")
    for path, dec in sorted(result.folder_decisions.items()):
        lines.append(f"### `{path}` — {dec.category.value} ({dec.confidence:.2f})\n")
        if dec.folder_description:
            lines.append(f"**Summary:** {dec.folder_description}\n")
        lines.append("**LLM Reasoning:**")
        lines.append("```")
        lines.append(dec.reason)
        lines.append("```")
        lines.append("")

    # --- 4. LLM Input/Output Log ---
    lines.append("## 4. LLM Call Log\n")
    lines.append(f"Total LLM calls: {len(classifier.debug_log)}\n")

    for i, entry in enumerate(classifier.debug_log):
        call_type = entry.get("call_type", "unknown")
        timestamp = entry.get("timestamp", "")
        lines.append(f"### Call {i + 1}: {call_type} ({timestamp})\n")

        lines.append("<details>")
        lines.append(f"<summary>System Prompt ({len(entry.get('system_prompt', ''))} chars)</summary>\n")
        lines.append("```")
        lines.append(entry.get("system_prompt", ""))
        lines.append("```")
        lines.append("</details>\n")

        lines.append("<details>")
        lines.append(f"<summary>User Prompt ({len(entry.get('user_prompt', ''))} chars)</summary>\n")
        lines.append("```")
        lines.append(entry.get("user_prompt", ""))
        lines.append("```")
        lines.append("</details>\n")

        parsed = entry.get("parsed_output")
        if parsed:
            lines.append("<details>")
            lines.append("<summary>Parsed Output</summary>\n")
            lines.append("```json")
            lines.append(json.dumps(parsed, indent=2, ensure_ascii=False))
            lines.append("```")
            lines.append("</details>\n")

        error = entry.get("error")
        if error:
            lines.append(f"**ERROR:** `{error}`\n")

        lines.append("")

    # --- 5. Organized Destination Tree ---
    lines.append("## 5. Organized Destination Tree\n")

    # Build tree structure from mappings
    dest_tree: dict = {}
    for m in result.mappings.values():
        parts = m.dest_rel.split("/")
        cur = dest_tree
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur.setdefault("__files__", []).append(parts[-1])

    lines.append("```")
    _render_dest_tree(dest_tree, lines, indent=0)
    lines.append("```")
    lines.append("")

    # --- 6. Full Mapping Table ---
    lines.append("## 6. File Mappings\n")
    lines.append("| Source | Destination | Category |")
    lines.append("|--------|-------------|----------|")
    for _, m in sorted(result.mappings.items()):
        lines.append(f"| `{m.source_rel}` | `{m.dest_rel}` | {m.category} |")
    lines.append("")

    # --- 7. Skipped Folders ---
    if result.skipped_folders:
        lines.append("## 7. Skipped Folders\n")
        for folder in sorted(result.skipped_folders):
            dec = result.folder_decisions.get(folder)
            reason = dec.reason[:100] + "..." if dec and len(dec.reason) > 100 else (dec.reason if dec else "—")
            lines.append(f"- `{folder}`: {reason}")
        lines.append("")

    # Write file
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"Report saved to {out_path}")


def _render_dest_tree(node: dict, lines: List[str], indent: int) -> None:
    """Render a nested dict as an indented tree."""
    pad = "  " * indent

    # Render files first
    files = node.get("__files__", [])
    for fn in sorted(files):
        lines.append(f"{pad}{fn}")

    # Render subdirectories
    for key in sorted(k for k in node if k != "__files__"):
        lines.append(f"{pad}{key}/")
        _render_dest_tree(node[key], lines, indent + 1)


# ====================================================================
#  Convenience Wrappers
# ====================================================================

def bfs_reorganize(
    course_root: str,
    db_path: str = DEFAULT_DB_PATH,
    model: str = "gpt-4.1-2025-04-14",
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    report_path: str = REPORT_MD_FILE,
    json_path: str = PLAN_JSON_FILE,
) -> TraversalResult:
    """One-call convenience function for the full pipeline."""
    db = CourseDB(db_path)
    db.connect()

    try:
        classifier = LLMClassifier(model=model)
        traverser = BFSTraverser(db, classifier, confidence_threshold)
        result = traverser.traverse(course_root)

        # Generate outputs
        generate_report(result, classifier, report_path)
        export_mappings_json(result, json_path)
    finally:
        db.close()

    return result


def print_classification_summary(result: TraversalResult) -> None:
    """Print a human-readable summary to stdout."""
    print("\n" + "=" * 70)
    print("BFS v2 — CLASSIFICATION SUMMARY")
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

    print(f"\nSkipped Folders ({len(result.skipped_folders)}):")
    for folder in result.skipped_folders:
        print(f"  - {folder}")

    print(f"\nFile Mappings: {len(result.mappings)}")
    print("=" * 70)


def export_mappings_json(result: TraversalResult, out_path: str) -> None:
    """Export mappings to a JSON file."""
    payload = {
        "folder_decisions": {
            path: {
                "category": dec.category.value,
                "confidence": dec.confidence,
                "reason": dec.reason,
                "is_mixed": dec.is_mixed,
                "folder_description": dec.folder_description,
            }
            for path, dec in result.folder_decisions.items()
        },
        "skipped_folders": result.skipped_folders,
        "mappings": [asdict(m) for m in result.mappings.values()],
        "stats": {
            "files_via_folder": result.files_classified_via_folder,
            "files_individual": result.files_classified_individually,
            "total_mappings": len(result.mappings),
        },
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"Exported plan to {out_path}")


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
        description="BFS v2 — Course File Reorganization Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python bfs_v2.py --source ./course --db ./file.db\n"
            "  python bfs_v2.py -s ./course -d ./file.db --model gpt-4.1-2025-04-14\n"
        ),
    )
    parser.add_argument("--source", "-s", required=True, help="Course root directory")
    parser.add_argument("--db", "-d", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
    parser.add_argument("--model", default="gpt-4.1-2025-04-14", help="OpenAI model")
    parser.add_argument(
        "--threshold", "-t", type=float, default=DEFAULT_CONFIDENCE_THRESHOLD,
        help=f"Confidence threshold (default: {DEFAULT_CONFIDENCE_THRESHOLD})",
    )
    parser.add_argument("--json-out", default=PLAN_JSON_FILE, help="JSON output file")
    parser.add_argument("--report", default=REPORT_MD_FILE, help="Markdown report output file")
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()

    source = os.path.abspath(args.source)
    if not os.path.isdir(source):
        print(f"Error: directory not found: {source}")
        sys.exit(1)

    if not os.environ.get("OPENAI_API_KEY"):
        print("Error: OPENAI_API_KEY not set")
        sys.exit(1)

    print("=" * 70)
    print("BFS v2 — Course File Reorganization Agent")
    print("=" * 70)
    print(f"Source:    {source}")
    print(f"DB:        {args.db}")
    print(f"Model:     {args.model}")
    print(f"Threshold: {args.threshold}")
    print(f"Report:    {args.report}")
    print("=" * 70)

    result = bfs_reorganize(
        course_root=source,
        db_path=args.db,
        model=args.model,
        confidence_threshold=args.threshold,
        report_path=args.report,
        json_path=args.json_out,
    )

    print_classification_summary(result)

    if args.verbose:
        print("\nMappings:")
        print("-" * 70)
        for _, m in sorted(result.mappings.items()):
            print(f"  {m.source_rel} -> {m.dest_rel}")


if __name__ == "__main__":
    main()
