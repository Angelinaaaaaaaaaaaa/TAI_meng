#!/usr/bin/env python3
"""
bfs_v3.py

BFS-based File Reorganization Agent — v3.

python3 bfs_v3.py \
  --source "/Users/runjiezhang/Desktop/CS 61A/61A_DB_ONLY_SKELETON/CS 61A_unstructured" \
  --db "/Users/runjiezhang/Desktop/CS 61A/file.db" \
  --model "gpt-5-mini-2025-08-07" \
  --execute \
  --dest "/Users/runjiezhang/Desktop/CS 61A/61A_reorganized_v3"

Edits:
  - No MAX_ANCESTOR_DEPTH or truncation for concatenated descriptions
  - Study destination has NO "lecture/" injection:
      study/<top_folder>/<tail>
  - Default model remains: gpt-5-mini-2025-08-07
  - File classification outputs are only: study/practice/support (handled in classify_v3.py).
"""

import json
import logging
import os
import shutil
import sqlite3
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Set, Union

from classify_v3 import (
    LLMClassifier,
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
LLM_DEBUG_LOG_FILE = "bfs_v3_llm_debug.json"
REPORT_MD_FILE = "bfs_v3_report.md"
PLAN_JSON_FILE = "bfs_v3_plan.json"
TREE_JSON_FILE = "bfs_v3_tree.json"


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
            "SELECT uuid, file_name, description, original_path, "
            "original_path, extra_info, file_hash FROM file"
        )
        rows = cur.fetchall()

        index: Dict[str, FileIndexEntry] = {}
        for r in rows:
            fname = r["file_name"] or ""
            if not fname:
                rel = r["original_path"] or ""
                fname = os.path.basename(rel) if rel else ""
            if not fname:
                continue

            entry = FileIndexEntry(
                uuid=r["uuid"],
                file_name=fname,
                description=r["description"] or "",
                original_path=r["original_path"],
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


def build_dest_rel(category: str, top_folder: str, tail: str) -> str:
    """Build destination relative path from category + top_folder + tail."""
    if category == "practice":
        return _join_rel("practice", top_folder, tail)
    if category == "support":
        return _join_rel("support", top_folder, tail)
    if category == "study":
        # IMPORTANT: no extra "lecture/" folder
        return _join_rel("study", top_folder, tail)
    return _join_rel(top_folder, tail)


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
            f"(mixed={result.is_mixed})"
        )

        # Accumulate ancestor context (NO depth cap)
        child_ancestors = list(my_ancestors)
        if result.folder_description:
            child_ancestors.append(result.folder_description)

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
                task_queue.append(child)
            ancestor_desc_map[item.path] = child_ancestors
            for f in item.files:
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
                task_queue.append(child)
            ancestor_desc_map[item.path] = child_ancestors
            for f in item.files:
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
            f"[BFS] File '{item.source_path}': {result.category.value}"
        )

        # classify_v3 guarantees file category is only study/practice/support
        classifications[item.source_path] = Classification(
            path=item.source_path,
            category=result.category,
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

    lines.append("# BFS v3 — Reorganization Report\n")
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
            }
            for path, dec in result.folder_decisions.items()
        },
        "dest_tree": _tree_to_serializable(dest_tree),
        "mappings": [
            {"source": m.source_rel, "dest": m.dest_rel, "category": m.category}
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
    dest_dir: Optional[str] = None,
) -> TraversalResult:
    db = CourseDB(db_path)
    db.connect()

    try:
        classifier = LLMClassifier(model=model)
        traverser = BFSTraverser(db, classifier)
        result = traverser.traverse(course_root)

        generate_report(result, classifier, report_path)
        export_mappings_json(result, json_path)
        export_tree_json(result, tree_path)

        if dest_dir:
            execute_moves(result, course_root, dest_dir)
    finally:
        db.close()

    return result


def print_classification_summary(result: TraversalResult) -> None:
    print("\n" + "=" * 70)
    print("BFS v3 — CLASSIFICATION SUMMARY")
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
        description="BFS v3 — Course File Reorganization Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Classify only (no file copies):\n"
            "  python bfs_v3.py --source ./61A --db ./file.db\n\n"
            "  # Classify + copy into a new folder (original untouched):\n"
            "  python bfs_v3.py --source ./61A --db ./file.db "
            "--execute --dest ./61A_reorganized\n"
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
    print("BFS v3 — Course File Reorganization Agent")
    print("=" * 70)
    print(f"Source:    {source}")
    print(f"DB:        {args.db}")
    print(f"Model:     {args.model}")
    print(f"Plan:      {args.json_out}")
    print(f"Tree:      {args.tree_out}")
    print(f"Report:    {args.report}")
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