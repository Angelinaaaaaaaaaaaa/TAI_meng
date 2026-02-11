#!/usr/bin/env python3
"""
file_organizer_v6.py

Top-level course folder classifier + reorganization planner.

Pipeline:
  A. Full recursive scan of the course root.
  B. Load file descriptions from SQLite (relative_path preferred, file_name fallback).
  C. Build a FolderNode tree (supports BFS traversal per top-level folder).
  D. Compute a FolderSummary for EVERY top-level folder (even future skips):
     - file_count (recursive), extension distribution, homogeneity check
     - immediate subfolders (all)
     - detected patterns via BFS: paired, sequential, exam-type
  E. Decide category per top-level folder (reason first, then category):
     1. Disabled skip rule placeholders (kept for future use).
     2. LLM classifies all folders into practice / study / support.
        Merges: base_reason + LLMReason.
  F. Build per-file move mappings:
     - practice/<top_folder>/<tail>
     - support/<top_folder>/<tail>
     - study/lecture/<top_folder>/<tail>
     - Special: top folder named "lecture" -> study/lecture/<tail>

Skip rules are DISABLED (placeholders kept).
LLM debug log saved to llm_debug_log.json.
"""

import os
import re
import json
import shutil
import sqlite3
from datetime import datetime
from typing import List, Optional, Dict, Tuple, Set
from dataclasses import dataclass, field, asdict
from collections import deque

from pydantic import BaseModel, Field
from openai import OpenAI


# ============================ Constants ============================

MAX_FILES_BEFORE_SKIP = 20
MIN_SEQUENTIAL_FOR_SKIP = 3  # placeholder for future "structured_sequence" skip
LLM_DEBUG_LOG_FILE = "llm_debug_log.json"
DEFAULT_DB_PATH = "CS 61A_metadata.db"

DEFAULT_EXCLUDE_DIRS = {
    ".git", "__pycache__", "node_modules",
    ".DS_Store", ".ipynb_checkpoints",
    "venv", ".venv", "env", ".env",
}

# Extensions considered "metadata/config" (ignored for homogeneity check unless they are the only files)
METADATA_EXTS = {
    ".yaml", ".yml", ".json", ".md", ".txt", ".gitignore", ".toml", ".ini"
}


# ============================ Pydantic models ============================

class SubfolderPattern(BaseModel):
    """Detected pattern among subfolders."""
    pattern_type: str = Field(..., description="e.g., 'paired', 'sequential', 'semester-based', 'exam-types'")
    description: str = Field(..., description="Human-readable description of the pattern")
    examples: List[str] = Field(default_factory=list, description="Example subfolder names showing the pattern")


class FolderSummary(BaseModel):
    """Summary information about a folder before classification."""
    folder_description: str = Field(..., description="Brief summary of what this folder contains")
    file_count: int = Field(..., description="Total number of files in the folder (recursive)")
    immediate_file_count: int = Field(..., description="Number of files directly in the folder")
    subfolder_count: int = Field(..., description="Number of immediate subfolders")
    subfolder_names: List[str] = Field(default_factory=list, description="Names of immediate subfolders")
    detected_patterns: List[SubfolderPattern] = Field(default_factory=list, description="Patterns detected among subfolders")
    file_types_homogeneous: bool = Field(..., description="Whether files are of similar type (by extension)")
    primary_file_types: List[str] = Field(default_factory=list, description="Main file types found (e.g., '.html', '.pdf', '.py')")


class FolderDecision(BaseModel):
    """Decision for one top-level folder (reason first, then category)."""
    folder_path: str = Field(..., description="Top-level folder path (relative to root)")
    summary: FolderSummary = Field(..., description="Summary of folder contents and structure")
    reason: str = Field(..., min_length=5, description="Detailed reasoning for the classification decision")
    category: str = Field(..., pattern="^(practice|study|support|skip)$")
    confidence: float = Field(..., ge=0.0, le=1.0)
    skip_reason: Optional[str] = Field(
        None,
        description="If category is 'skip', explains why (too_many_files, structured_sequence, etc.)"
    )


# LLM output schema — reason MUST come before category/confidence.
# The LLM is instructed to think through reasoning first, then decide.
class LLMFolderDecision(BaseModel):
    folder_path: str = Field(..., description="Exact folder path from input")
    reason: str = Field(
        ...,
        min_length=10,
        description=(
            "MUST be filled FIRST. Detailed reasoning about what this folder contains, "
            "its purpose, and why it belongs in the chosen category. "
            "Think step-by-step BEFORE choosing a category."
        ),
    )
    category: str = Field(
        ...,
        pattern="^(practice|study|support)$",
        description="Category decided AFTER reasoning. One of: practice, study, support.",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score (0-1) decided AFTER reasoning.",
    )


class LLMFolderDecisionBatch(BaseModel):
    decisions: List[LLMFolderDecision]


# ============================ Internal data structures ============================

@dataclass
class FileMeta:
    """Small file object with optional description."""
    source_path: str        # relative path from root, e.g. "disc/disc01/x.html"
    folder_path: str        # parent folder relative path, e.g. "disc/disc01"
    file_name: str
    description: Optional[str] = None


@dataclass
class FolderNode:
    """Tree node for a folder."""
    path: str
    name: str
    files: List[FileMeta] = field(default_factory=list)
    children: Dict[str, "FolderNode"] = field(default_factory=dict)


@dataclass
class FileMapping:
    """Planned move for one file."""
    source_rel: str
    dest_rel: str
    top_folder: str
    category: str
    reason: str


@dataclass
class LLMDebugEntry:
    """Debug entry for LLM call."""
    timestamp: str
    call_type: str  # "folder_classification"
    system_prompt: str
    user_prompt: str
    raw_output: Optional[str]
    parsed_output: Optional[dict]
    error: Optional[str]


@dataclass
class OrganizeResult:
    """Full output used by reorganize_files.py."""
    folder_decisions: Dict[str, FolderDecision]
    skipped_folders: List[str]
    mappings: List[FileMapping]
    llm_debug_log: List[LLMDebugEntry] = field(default_factory=list)


# ============================ FileOrganizer ============================

class FileOrganizer:
    """Main API used by reorganize_files.py and standalone CLI."""

    def __init__(self, model: str = "gpt-5.2-2025-12-11"):
        if not os.environ.get("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY environment variable not set")
        self.model = model
        self.client = OpenAI()
        self.llm_debug_log: List[LLMDebugEntry] = []

    # ------------------------ Public entry ------------------------ #

    def organize(
        self,
        source_path: str,
        max_depth: Optional[int] = None,
        exclude_dirs: Optional[Set[str]] = None,
        batch_size: int = 100,
        db_path: Optional[str] = None,
    ) -> OrganizeResult:
        """Analyze and build a plan. Does not move files."""
        source_path = os.path.abspath(source_path)
        if not os.path.isdir(source_path):
            raise ValueError(f"Source directory not found: {source_path}")

        exclude_dirs = set(exclude_dirs or DEFAULT_EXCLUDE_DIRS)
        self.llm_debug_log = []

        files_on_disk = self._scan_directory(source_path, max_depth=max_depth, exclude_dirs=exclude_dirs)
        print(f"Scanned {len(files_on_disk)} files from disk.")

        # Default DB: look for DEFAULT_DB_PATH relative to source, then cwd
        if db_path is None:
            candidate = os.path.join(source_path, DEFAULT_DB_PATH)
            if os.path.exists(candidate):
                db_path = candidate
            elif os.path.exists(DEFAULT_DB_PATH):
                db_path = os.path.abspath(DEFAULT_DB_PATH)
        path2desc = self._load_descriptions_safe(source_path, db_path=db_path)

        root = self._build_tree(source_path, files_on_disk, path2desc)

        folder_decisions, _folder_summaries = self._classify_top_level_folders(root)

        skipped = [p for p, d in folder_decisions.items() if d.category == "skip"]

        mappings = self._build_mappings(root, folder_decisions)

        self._save_llm_debug_log()

        return OrganizeResult(
            folder_decisions=folder_decisions,
            skipped_folders=skipped,
            mappings=mappings,
            llm_debug_log=self.llm_debug_log,
        )

    def generate_report(self, result: OrganizeResult) -> None:
        """Print a Markdown report to stdout."""
        md = self._build_markdown_report(result)
        print(md)

    def export_to_json(self, result: OrganizeResult, json_file: str) -> None:
        """Export the full plan to a JSON file."""
        payload = {
            "folder_decisions": {},
            "skipped_folders": result.skipped_folders,
            "mappings": [asdict(m) for m in result.mappings],
        }

        for path, dec in sorted(result.folder_decisions.items(), key=lambda x: x[0]):
            dec_dict = {
                "folder_path": dec.folder_path,
                "summary": dec.summary.model_dump(),
                "reason": dec.reason,
                "category": dec.category,
                "confidence": dec.confidence,
            }
            if dec.skip_reason:
                dec_dict["skip_reason"] = dec.skip_reason
            payload["folder_decisions"][path] = dec_dict

        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def export_tree_to_markdown(self, result: OrganizeResult, md_file: str) -> None:
        """Export a destination tree preview in Markdown.

        Structure:
        1. Folder-level summary table (category, file count, patterns per top folder)
        2. Folder-only destination tree (no individual files)
        3. Full file-level destination tree
        """
        # Build full tree (with files)
        full_tree: dict = {}
        for m in result.mappings:
            parts = m.dest_rel.split("/")
            cur = full_tree
            for p in parts[:-1]:
                cur = cur.setdefault(p, {})
            cur.setdefault("__files__", []).append(parts[-1])

        # Build folder-only tree (directories only, no __files__ keys)
        folder_tree: dict = {}
        for m in result.mappings:
            parts = m.dest_rel.split("/")
            # Only include directory parts (skip the filename at the end)
            cur = folder_tree
            for p in parts[:-1]:
                cur = cur.setdefault(p, {})

        lines: List[str] = ["# Planned Destination Structure\n"]

        # --- Section 1: Folder-level summary ---
        lines.append("## Folder Summary\n")
        lines.append("| Source Folder | Category | Confidence | Files | Destination |")
        lines.append("|---------------|----------|------------|-------|-------------|")
        for path, dec in sorted(result.folder_decisions.items(), key=lambda x: x[0]):
            folder_name = os.path.basename(path)
            if dec.category == "practice":
                dest = f"practice/{folder_name}"
            elif dec.category == "study":
                dest = f"study/lecture/{folder_name}" if folder_name != "lecture" else "study/lecture"
            elif dec.category == "support":
                dest = f"support/{folder_name}"
            else:
                dest = "(skipped)"
            lines.append(
                f"| `{path}` | {dec.category} | {dec.confidence:.2f} | {dec.summary.file_count} | `{dest}` |"
            )
        lines.append("")

        # --- Section 2: Folder-only tree ---
        lines.append("## Folder Structure (no files)\n")
        lines.append("```")
        self._render_folder_tree(folder_tree, lines, indent=0, max_children=3, full_depth=2)
        lines.append("```")
        lines.append("")

        # --- Section 3: Full file-level tree ---
        lines.append("## Full Tree (with files)\n")
        self._render_tree_md(full_tree, lines, indent=0)

        with open(md_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def move_files(
        self,
        result: OrganizeResult,
        source_root: str,
        dest_root: str,
        dry_run: bool = True,
    ) -> Dict[str, int]:
        """Move files according to result.mappings."""
        source_root = os.path.abspath(source_root)
        dest_root = os.path.abspath(dest_root)

        stats = {"moved": 0, "skipped": 0, "errors": 0}

        for m in result.mappings:
            src = os.path.join(source_root, m.source_rel)
            dst = os.path.join(dest_root, m.dest_rel)

            if not os.path.exists(src):
                stats["skipped"] += 1
                continue

            try:
                if dry_run:
                    stats["moved"] += 1
                    continue

                os.makedirs(os.path.dirname(dst), exist_ok=True)

                final_dst = dst
                if os.path.exists(final_dst):
                    final_dst = self._dedup_path(final_dst)

                shutil.move(src, final_dst)
                stats["moved"] += 1
            except Exception:
                stats["errors"] += 1

        return stats

    # ============================ LLM Debug Logging ============================

    def _log_llm_call(
        self,
        call_type: str,
        system_prompt: str,
        user_prompt: str,
        raw_output: Optional[str] = None,
        parsed_output: Optional[dict] = None,
        error: Optional[str] = None,
    ) -> None:
        entry = LLMDebugEntry(
            timestamp=datetime.now().isoformat(),
            call_type=call_type,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            raw_output=raw_output,
            parsed_output=parsed_output,
            error=error,
        )
        self.llm_debug_log.append(entry)
        print(f"[LLM DEBUG] Logged {call_type} call at {entry.timestamp}")

    def _save_llm_debug_log(self) -> None:
        if not self.llm_debug_log:
            return

        log_entries = []
        for entry in self.llm_debug_log:
            log_entries.append({
                "timestamp": entry.timestamp,
                "call_type": entry.call_type,
                "system_prompt": entry.system_prompt,
                "user_prompt": entry.user_prompt,
                "raw_output": entry.raw_output,
                "parsed_output": entry.parsed_output,
                "error": entry.error,
            })

        with open(LLM_DEBUG_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(log_entries, f, ensure_ascii=False, indent=2)

        print(f"[LLM DEBUG] Saved {len(log_entries)} entries to {LLM_DEBUG_LOG_FILE}")

    def _safe_resp_to_string(self, resp) -> str:
        try:
            return str(resp)
        except Exception:
            try:
                return repr(resp)
            except Exception:
                return "<unstringifiable response>"

    # ============================ Scan / DB / Tree ============================

    def _scan_directory(
        self,
        root_dir: str,
        max_depth: Optional[int],
        exclude_dirs: Set[str],
    ) -> List[str]:
        """Scan all files under root_dir and return relative paths."""
        out: List[str] = []
        root_dir = os.path.abspath(root_dir)

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

    def _load_descriptions_safe(self, source_path: str, db_path: Optional[str]) -> Dict[str, str]:
        """Load descriptions from SQLite.

        Strategy: query all rows from DB `file` table, build a lookup keyed by
        the *filename* extracted from relative_path (i.e. the last path segment).
        This handles the common case where DB relative_path has a different prefix
        (e.g. "CS 61A/study/lec03/slides/03.py") than the on-disk relative path
        (e.g. "lecture/03.py").

        For duplicate filenames, the first row wins (most DB entries are unique by name).
        """
        if not db_path:
            print("[DB] No SQLite DB path provided. Proceeding with filenames only.")
            return {}

        if not os.path.exists(db_path):
            print(f"[DB] Provided DB not found: {db_path}. Proceeding with filenames only.")
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

                # Key by filename extracted from relative_path (last segment)
                rel = r["relative_path"] or ""
                fname = os.path.basename(rel) if rel else (r["file_name"] or "")

                if fname and fname not in mapping:
                    mapping[fname] = desc

            print(f"[DB] Loaded {len(mapping)} descriptions (keyed by filename) from {db_path}.")

        except sqlite3.OperationalError as e:
            print(f"[DB] ERROR querying file table: {e}")
            mapping = {}

        finally:
            conn.close()

        return mapping

    def _build_tree(self, root_dir: str, files_on_disk: List[str], path2desc: Dict[str, str]) -> FolderNode:
        """Build a folder tree and attach description to each file."""
        root = FolderNode(path=".", name="root")
        matched = 0
        unmatched_examples: List[str] = []

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

            # Lookup by filename (DB is keyed by filename)
            desc = path2desc.get(fname)

            if desc:
                matched += 1
            elif path2desc and len(unmatched_examples) < 10:
                unmatched_examples.append(fname)

            node.files.append(
                FileMeta(
                    source_path=rel_path,
                    folder_path=folder,
                    file_name=fname,
                    description=desc,
                )
            )

        total = len(files_on_disk)
        if path2desc:
            print(f"[DB MATCH] {matched}/{total} files matched descriptions.")
            if unmatched_examples:
                print(f"[DB MATCH] Unmatched examples: {unmatched_examples}")
        return root

    # ============================ Summary / Patterns / Skip ============================

    def _collect_all_files_bfs(self, node: FolderNode) -> List[FileMeta]:
        """Collect all files under node recursively in BFS order."""
        out: List[FileMeta] = []
        q: deque[FolderNode] = deque([node])
        while q:
            cur = q.popleft()
            out.extend(cur.files)
            for child in cur.children.values():
                q.append(child)
        return out

    def _get_all_file_extensions_bfs(self, node: FolderNode) -> Dict[str, int]:
        ext_counts: Dict[str, int] = {}
        for f in self._collect_all_files_bfs(node):
            ext = os.path.splitext(f.file_name)[1].lower() or "(no ext)"
            ext_counts[ext] = ext_counts.get(ext, 0) + 1
        return ext_counts

    def _detect_subfolder_patterns_bfs(self, node: FolderNode) -> List[SubfolderPattern]:
        """Detect patterns among subfolders (immediate + deeper via BFS)."""
        patterns: List[SubfolderPattern] = []

        q: deque[FolderNode] = deque([node])

        sol_prefixes = ("sol-", "sol_", "solution-", "solution_", "solutions-", "solutions_")
        sol_suffixes = ("-sol", "_sol", "-solution", "_solution", "-solutions", "_solutions")

        paired_examples: List[str] = []
        paired_count = 0

        while q:
            cur = q.popleft()
            child_names = list(cur.children.keys())
            child_set = set(child_names)

            for name in child_names:
                low = name.lower()

                for pref in sol_prefixes:
                    if low.startswith(pref):
                        base = name[len(pref):]
                        if base in child_set and base:
                            paired_count += 1
                            if len(paired_examples) < 12:
                                paired_examples.append(f"{base} <-> {name}")

                for suf in sol_suffixes:
                    if low.endswith(suf):
                        base = name[: -len(suf)]
                        if base in child_set and base:
                            paired_count += 1
                            if len(paired_examples) < 12:
                                paired_examples.append(f"{base} <-> {name}")

            for ch in cur.children.values():
                q.append(ch)

        if paired_count > 0:
            patterns.append(SubfolderPattern(
                pattern_type="paired",
                description=f"Detected assignment/solution subfolder pairing ({paired_count} matches) via BFS",
                examples=sorted(list(set(paired_examples)))[:12]
            ))

        # Sequential immediate pattern
        immediate = list(node.children.keys())
        numbered = re.compile(r"^([A-Za-z]+)(\d+)$")
        groups: Dict[str, List[int]] = {}
        for n in immediate:
            m = numbered.match(n)
            if not m:
                continue
            pref, num = m.group(1), int(m.group(2))
            groups.setdefault(pref, []).append(num)

        for pref, nums in groups.items():
            if len(nums) >= 3:
                nums.sort()
                patterns.append(SubfolderPattern(
                    pattern_type="sequential",
                    description=f"Immediate sequential folders for prefix '{pref}' ({len(nums)} items)",
                    examples=[f"{pref}{x:02d}" for x in nums[:3]]
                ))

        # Exam keywords immediate
        exam_keys = {"mt1", "mt2", "mt3", "midterm", "final", "diagnostic", "practice"}
        exam_folders = [n for n in immediate if n.lower() in exam_keys]
        if exam_folders:
            patterns.append(SubfolderPattern(
                pattern_type="exam-types",
                description=f"Immediate exam-type folders found: {', '.join(sorted(exam_folders))}",
                examples=sorted(exam_folders)[:8]
            ))

        return patterns

    def _check_file_type_homogeneity(self, ext_counts: Dict[str, int]) -> Tuple[bool, List[str]]:
        if not ext_counts:
            return True, []

        sorted_exts = sorted(ext_counts.items(), key=lambda x: (-x[1], x[0]))
        primary = [e for e, _ in sorted_exts[:3]]

        content_exts = {e for e in ext_counts.keys() if e not in METADATA_EXTS}
        if not content_exts:
            return True, primary

        return (len(content_exts) == 1), primary

    def _build_folder_summary(self, node: FolderNode) -> FolderSummary:
        """Build folder summary with BFS patterns."""
        all_files = self._collect_all_files_bfs(node)
        file_count = len(all_files)
        ext_counts = self._get_all_file_extensions_bfs(node)
        patterns = self._detect_subfolder_patterns_bfs(node)
        is_homogeneous, primary = self._check_file_type_homogeneity(ext_counts)

        subfolder_names = sorted(list(node.children.keys()))
        desc_parts = [f"Contains {file_count} files"]
        if node.children:
            desc_parts.append(f"{len(node.children)} subfolders")
        if patterns:
            desc_parts.append("Patterns: " + "; ".join([p.description for p in patterns]))

        return FolderSummary(
            folder_description=" | ".join(desc_parts),
            file_count=file_count,
            immediate_file_count=len(node.files),
            subfolder_count=len(node.children),
            subfolder_names=subfolder_names,
            detected_patterns=patterns,
            file_types_homogeneous=is_homogeneous,
            primary_file_types=primary,
        )

    def _skip_rule_placeholder(self, summary: FolderSummary) -> Tuple[bool, Optional[str]]:
        """
        Skip rules are DISABLED in this version.
        Placeholders kept for future use:
        - too_many_files: file_count > MAX_FILES_BEFORE_SKIP
        - structured_sequence: lots of sequential/paired folders
        - mixed_content: not homogeneous by extension
        """
        # (DISABLED) Rule 1: Too many files
        # if summary.file_count > MAX_FILES_BEFORE_SKIP:
        #     return True, "too_many_files"

        # (DISABLED) Rule 2: Structured sequences (sequential/paired)
        # if self._has_structured_sequence(summary):
        #     return True, "structured_sequence"

        # (DISABLED) Rule 3: Mixed content types by extension
        # if not summary.file_types_homogeneous:
        #     return True, "mixed_content"

        return False, None

    def _has_structured_sequence(self, summary: FolderSummary) -> bool:
        """Placeholder helper for future structured_sequence skip."""
        for pattern in summary.detected_patterns:
            if pattern.pattern_type == "paired":
                return True
            if pattern.pattern_type == "sequential":
                match = re.search(r"\((\d+) items\)", pattern.description)
                if match and int(match.group(1)) >= MIN_SEQUENTIAL_FOR_SKIP:
                    return True
        return False

    def _build_base_reason(self, summary: FolderSummary) -> str:
        """Build base_reason from summary + detected patterns."""
        lines = [
            "FolderSummary:",
            summary.folder_description,
            f"- file_count={summary.file_count}, immediate_file_count={summary.immediate_file_count}",
            f"- subfolder_count={summary.subfolder_count}",
            f"- primary_file_types={summary.primary_file_types}",
            f"- file_types_homogeneous={summary.file_types_homogeneous}",
        ]
        if summary.subfolder_names:
            lines.append(f"- subfolders_count={len(summary.subfolder_names)}")
            lines.append(f"- subfolders_sample={summary.subfolder_names[:20]}")
        if summary.detected_patterns:
            lines.append("SubfolderPatterns:")
            for p in summary.detected_patterns:
                lines.append(f"- {p.pattern_type}: {p.description}")
                if p.examples:
                    lines.append(f"  examples: {p.examples[:10]}")
        return "\n".join(lines)

    # ============================ LLM Classification ============================

    def _folder_prompt_block(self, node: FolderNode, summary: FolderSummary) -> str:
        """
        Build a prompt block for one top-level folder.
        Includes structural info AND a BFS sample of files with descriptions
        (following v5 style: file_name :: full description).
        """
        lines: List[str] = [
            f"Folder: {node.path}",
            f"Name: {node.name}",
            f"TotalFiles: {summary.file_count}",
            f"ImmediateFiles: {summary.immediate_file_count}",
            f"SubfolderCount: {summary.subfolder_count}",
            f"HasSubfolders: {'yes' if node.children else 'no'}",
            f"FileTypesHomogeneous: {'yes' if summary.file_types_homogeneous else 'no'}",
            f"PrimaryFileTypes: {', '.join(summary.primary_file_types) or 'N/A'}",
        ]

        if summary.subfolder_names:
            lines.append("Subfolders (immediate):")
            lines.append("  " + ", ".join(summary.subfolder_names[:30]))
            if len(summary.subfolder_names) > 30:
                lines.append(f"  ... and {len(summary.subfolder_names) - 30} more")

        if summary.detected_patterns:
            lines.append("DetectedPatterns:")
            for p in summary.detected_patterns:
                lines.append(f"  - {p.pattern_type}: {p.description}")
                if p.examples:
                    lines.append(f"    Examples: {', '.join(p.examples[:8])}")

        # BFS file listing with descriptions (like v5's approach)
        all_files = self._collect_all_files_bfs(node)
        if all_files:
            # Cap at 50 files to keep prompt manageable
            max_files_in_prompt = 50
            lines.append(f"Files (name + full description, up to {min(len(all_files), max_files_in_prompt)} shown):")
            for f in all_files[:max_files_in_prompt]:
                desc = (f.description or "").replace("\n", " ").strip()
                if not desc:
                    desc = "[no description]"
                lines.append(f"  - {f.file_name} :: {desc}")
            if len(all_files) > max_files_in_prompt:
                lines.append(f"  ... and {len(all_files) - max_files_in_prompt} more files")

        return "\n".join(lines)

    def _system_prompt_for_folders(self) -> str:
        """
        System prompt for LLM classification.
        Follows v5's detailed, rule-numbered style.
        """
        return (
            "You are classifying ONLY TOP-LEVEL course folders into three buckets:\n"
            "- practice: Students DO or PRODUCE work — homework (hw), labs, projects.\n"
            "  This also includes exam solutions/explanations (e.g., midterm walkthroughs).\n"
            "- study: Instructional learning content that students READ, WATCH, or REVIEW.\n"
            "  This includes: lectures, lecture slides/notes/PDFs, readings, videos,\n"
            "  discussion/section materials and folders containing lecture slides/PDFs/code.\n"
            "- support: global course support like syllabus, past exams, textbooks, tools/how-to docs.\n\n"
            "Key distinction: 'practice' = student-produced assignments (hw, lab, project).\n"
            "'study' = instructor-provided learning material.\n"
            "Discussion worksheets are study material even when they contain problems, because they are\n"
            "part of the instructor-led section flow, not graded student submissions.\n\n"
            "Rules:\n"
            "1. You receive descriptions for top-level folders under the course root.\n"
            "2. You are told whether a folder HasSubfolders, but this is ONLY a hint.\n"
            "   Folders with subfolders MAY still be classified as practice, study, or support\n"
            "   if their overall purpose is clear.\n"
            "3. Your primary goal is to assign ONE best category per folder based on its\n"
            "   main purpose: practice / study / support.\n"
            "4. Think top-down: classify the folder as a whole, not per-file.\n"
            "5. When you choose 'study', you are implicitly saying this folder belongs\n"
            "   under 'study/lecture/<folder_name>', so it must be clearly lecture-related content.\n"
            "6. CRITICAL — for EACH folder you MUST reason FIRST, then decide:\n"
            "   a. Write a detailed 'reason' field explaining what the folder contains,\n"
            "      what its educational purpose is, and why it fits a particular category.\n"
            "   b. Only AFTER writing the reason, fill in 'category' and 'confidence'.\n"
            "   c. The reason must logically support the chosen category.\n"
            "   DO NOT pick a category first and then justify it — think first, decide second.\n"
            "7. Return STRICT JSON: {\"decisions\": [...]}. Each element fields IN ORDER:\n"
            "   folder_path, reason, category, confidence.\n"
            "   reason comes BEFORE category — write it first, decide category after.\n"
            "8. folder_path MUST match exactly the string shown after 'Folder:' in the input.\n"
        )

    def _classify_top_level_folders(
        self,
        root: FolderNode
    ) -> Tuple[Dict[str, FolderDecision], Dict[str, FolderSummary]]:
        """
        Classify top-level folders:
        1. Compute summary (always, for every folder).
        2. Skip rules (currently disabled, placeholder).
        3. LLM classifies all remaining into practice/study/support.
        """
        top_nodes = list(root.children.values())
        decisions: Dict[str, FolderDecision] = {}
        summaries: Dict[str, FolderSummary] = {}

        to_llm: List[Tuple[FolderNode, FolderSummary]] = []

        for node in top_nodes:
            summary = self._build_folder_summary(node)
            summaries[node.path] = summary

            base_reason = self._build_base_reason(summary)

            # Skip rules (disabled placeholder)
            should_skip, skip_key = self._skip_rule_placeholder(summary)
            if should_skip:
                extra = f"SkipRule: {skip_key}."
                decisions[node.path] = FolderDecision(
                    folder_path=node.path,
                    summary=summary,
                    reason=base_reason + "\n" + extra,
                    category="skip",
                    confidence=1.0,
                    skip_reason=skip_key,
                )
                continue

            to_llm.append((node, summary))

        if not to_llm:
            return decisions, summaries

        # Build prompt blocks and call LLM
        folder_blocks = [self._folder_prompt_block(n, s) for n, s in to_llm]
        system_prompt = self._system_prompt_for_folders()
        user_prompt = (
            f"You will receive {len(folder_blocks)} top-level folders.\n"
            "For EACH folder described below, produce EXACTLY ONE entry in\n"
            "`decisions` with fields in this order: folder_path, reason, category, confidence.\n\n"
            "IMPORTANT: For each folder, you MUST write the 'reason' field FIRST — analyze\n"
            "what the folder contains and its educational purpose. Only AFTER completing\n"
            "the reasoning should you decide on 'category' and 'confidence'.\n"
            "先给reason，再生成category和confidence！\n\n"
            "Top-level folders:\n\n" + "\n\n---\n\n".join(folder_blocks)
        )

        print(f"[LLM] Calling {self.model} for folder classification ({len(to_llm)} folders)...")

        try:
            resp = self.client.responses.parse(
                model=self.model,
                instructions=system_prompt,
                input=[
                    {"role": "user", "content": user_prompt},
                ],
                text_format=LLMFolderDecisionBatch,
            )
            batch: LLMFolderDecisionBatch = resp.output_parsed

            self._log_llm_call(
                call_type="folder_classification",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                raw_output=self._safe_resp_to_string(resp),
                parsed_output={"decisions": [d.model_dump() for d in batch.decisions]},
                error=None,
            )

            # Merge LLM decisions with base_reason
            for llm_dec in batch.decisions:
                summary = summaries.get(llm_dec.folder_path)
                if summary is None:
                    print(f"[WARN] LLM returned folder_path '{llm_dec.folder_path}' not found in summaries, skipping.")
                    continue

                base_reason = self._build_base_reason(summary)
                merged_reason = base_reason + "\nLLMReason: " + llm_dec.reason

                decisions[llm_dec.folder_path] = FolderDecision(
                    folder_path=llm_dec.folder_path,
                    summary=summary,
                    reason=merged_reason,
                    category=llm_dec.category,
                    confidence=float(llm_dec.confidence),
                    skip_reason=None,
                )

        except Exception as e:
            self._log_llm_call(
                call_type="folder_classification",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                raw_output=None,
                parsed_output=None,
                error=str(e),
            )
            raise

        return decisions, summaries

    # ============================ Mapping / Report / Helpers ============================

    def _build_mappings(self, root: FolderNode, folder_decisions: Dict[str, FolderDecision]) -> List[FileMapping]:
        """Build per-file mappings for non-skip top-level folders."""
        mappings: List[FileMapping] = []

        for _top_name, top_node in root.children.items():
            dec = folder_decisions.get(top_node.path)
            if not dec:
                continue
            if dec.category == "skip":
                continue

            all_files = self._collect_all_files_bfs(top_node)
            for f in all_files:
                rel = f.source_path
                tail = rel[len(top_node.path):].lstrip("/")
                top_folder_name = os.path.basename(top_node.path)

                dest_rel = self._dest_rel_for(dec.category, top_folder_name, tail)
                mappings.append(FileMapping(
                    source_rel=rel,
                    dest_rel=dest_rel,
                    top_folder=top_folder_name,
                    category=dec.category,
                    reason=dec.reason,
                ))

        return mappings

    def _dest_rel_for(self, category: str, top_folder_name: str, tail: str) -> str:
        """Map category + top_folder + tail to destination relative path."""
        if category == "practice":
            return self._join_rel("practice", top_folder_name, tail)
        if category == "support":
            return self._join_rel("support", top_folder_name, tail)
        if category == "study":
            # Special case: top folder named "lecture" -> study/lecture/<tail>
            if top_folder_name == "lecture":
                return self._join_rel("study", "lecture", tail)
            return self._join_rel("study", "lecture", top_folder_name, tail)
        return self._join_rel(top_folder_name, tail)

    def _build_markdown_report(self, result: OrganizeResult) -> str:
        """Build a readable Markdown report."""
        lines: List[str] = []
        lines.append("# File Organizer Report (Top-level v6)\n")

        lines.append("## Summary\n")
        lines.append("| Folder | Category | Files | Homogeneous | Patterns | Confidence |")
        lines.append("|--------|----------|-------|-------------|----------|------------|")

        for path, dec in sorted(result.folder_decisions.items(), key=lambda x: x[0]):
            pats = ", ".join([p.pattern_type for p in dec.summary.detected_patterns]) or "N/A"
            lines.append(
                f"| `{path}` | {dec.category} | {dec.summary.file_count} | "
                f"{'yes' if dec.summary.file_types_homogeneous else 'no'} | {pats} | {dec.confidence:.2f} |"
            )

        if result.skipped_folders:
            lines.append("\n## Skipped Folders\n")
            for p in sorted(result.skipped_folders):
                dec = result.folder_decisions[p]
                lines.append(f"- `{p}`: {dec.skip_reason or 'skip'}")

        lines.append("\n## Detailed Decisions\n")
        for path, dec in sorted(result.folder_decisions.items(), key=lambda x: x[0]):
            lines.append(f"### `{path}`")
            lines.append(f"- Category: **{dec.category}**")
            if dec.skip_reason:
                lines.append(f"- Skip reason: **{dec.skip_reason}**")
            lines.append(f"- Summary: {dec.summary.folder_description}")
            lines.append("")
            lines.append("```")
            lines.append(dec.reason)
            lines.append("```")
            lines.append("")

        lines.append("\n## Planned Moves (count)\n")
        lines.append(f"- Total planned file moves (excluding skip folders): **{len(result.mappings)}**")

        if result.llm_debug_log:
            lines.append("\n## LLM Debug\n")
            lines.append(f"- Total LLM calls: {len(result.llm_debug_log)}")
            lines.append(f"- Debug log saved to: `{LLM_DEBUG_LOG_FILE}`")

        return "\n".join(lines)

    def _render_tree_md(self, node: dict, lines: List[str], indent: int) -> None:
        pad = "  " * indent
        files = node.get("__files__", [])
        for fn in sorted(files):
            lines.append(f"{pad}- {fn}")

        for k in sorted([x for x in node.keys() if x != "__files__"]):
            lines.append(f"{pad}- **{k}/**")
            self._render_tree_md(node[k], lines, indent + 1)

    def _render_folder_tree(
        self, node: dict, lines: List[str], indent: int,
        max_children: int = 3, full_depth: int = 3,
    ) -> None:
        """Render folder-only tree (no files).

        The first `full_depth` levels (0-indexed indent) always show ALL children.
        Deeper levels cap at `max_children` with a '... and N more' note.
        """
        children = sorted(node.keys())

        if indent < full_depth:
            # Show all children at the first few levels
            shown = children
            remaining = 0
        else:
            shown = children[:max_children]
            remaining = len(children) - len(shown)

        for k in shown:
            lines.append("  " * indent + f"{k}/")
            self._render_folder_tree(node[k], lines, indent + 1, max_children, full_depth)

        if remaining > 0:
            lines.append("  " * indent + f"... and {remaining} more folders")

    def _dedup_path(self, path: str) -> str:
        base, ext = os.path.splitext(path)
        i = 1
        while True:
            candidate = f"{base}({i}){ext}"
            if not os.path.exists(candidate):
                return candidate
            i += 1

    def _join_rel(self, *parts: str) -> str:
        clean = []
        for p in parts:
            if p is None:
                continue
            p = str(p).strip().replace("\\", "/")
            if p == "":
                continue
            clean.append(p)
        return "/".join(clean)


# ============================ CLI entry ============================

def main():
    """Standalone CLI entrypoint for file_organizer_v6."""
    import sys
    import argparse

    parser = argparse.ArgumentParser(
        description="Top-level course folder classifier (v6)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python file_organizer_v6.py --source ./my_course\n"
            "  python file_organizer_v6.py --source ./my_course --db ./my_course/descriptions.db\n"
            "  python file_organizer_v6.py --source ./my_course --model gpt-4o\n"
        ),
    )
    parser.add_argument("--source", required=True, help="Source directory to organize")
    parser.add_argument("--db", dest="db_path", default=None, help="SQLite DB path (default: CS 61A_metadata.db)")
    parser.add_argument("--model", default="gpt-5.2-2025-12-11", help="OpenAI model (default: gpt-5.2-2025-12-11)")
    parser.add_argument("--depth", type=int, default=None, help="Max scan depth (default: unlimited)")
    parser.add_argument("--json-out", default="organization_plan.json", help="JSON plan output file")
    parser.add_argument("--md-out", default="organization_structure.md", help="Markdown tree output file")

    args = parser.parse_args()

    source_path = os.path.abspath(args.source)
    if not os.path.isdir(source_path):
        print(f"Error: Source directory does not exist: {source_path}")
        sys.exit(1)

    if not os.environ.get("OPENAI_API_KEY"):
        print("Error: OPENAI_API_KEY environment variable not set")
        print("  export OPENAI_API_KEY='your-api-key-here'")
        sys.exit(1)

    print("=" * 70)
    print("FILE ORGANIZER v6 - Top-level Folder Classifier")
    print("=" * 70)
    print(f"Source: {source_path}")
    print(f"Model:  {args.model}")
    print(f"Depth:  {'unlimited' if args.depth is None else args.depth}")
    if args.db_path:
        print(f"DB:     {args.db_path}")
    print("=" * 70)

    organizer = FileOrganizer(model=args.model)

    print(f"\nAnalyzing files in: {source_path}\n")

    result = organizer.organize(
        source_path,
        max_depth=args.depth,
        db_path=args.db_path,
    )

    # Print report
    print("\n" + "=" * 70)
    organizer.generate_report(result)

    # Export plan
    organizer.export_to_json(result, args.json_out)
    organizer.export_tree_to_markdown(result, args.md_out)
    print(f"\nExported plan to: {args.json_out}")
    print(f"Exported tree to: {args.md_out}")

    print(f"\nDone. {len(result.mappings)} file moves planned, {len(result.skipped_folders)} folders skipped.")


if __name__ == "__main__":
    main()
