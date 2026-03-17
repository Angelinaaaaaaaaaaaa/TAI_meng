#!/usr/bin/env python3
"""
second_pass_organizer.py

Second-pass organizer for BFS-reorganized course directories.

After bfs_v3.py places files into category subdirs (practice/hw/, study/disc/, etc.),
this script reorganizes each subdirectory into a clean numbered-subfolder hierarchy
matching the groundtruth (hw01/, disc02/, slides03/, ...).

Fully LLM-driven — no hardcoded regex. One LLM call per directory determines the
numbering scheme and which items need to move. Generalizable to any section directory.

Usage:
  # Dry-run (default): print plan without moving anything
  python second_pass_organizer.py --root ./61A_reorganized_v4 --db ./file.db

  # Execute the moves
  python second_pass_organizer.py --root ./61A_reorganized_v4 --db ./file.db --execute

  # Only specific sections
  python second_pass_organizer.py --root ./61A_reorganized_v4 --db ./file.db --sections hw disc

  # Add a custom section directory
  python second_pass_organizer.py --root ./61A_reorganized_v4 --db ./file.db \
      --section-paths "extra=study/extra"
"""

import argparse
import json
import logging
import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from openai import OpenAI
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ============================================================
#  Default section directory map (relative to reorganized root)
# ============================================================

DEFAULT_SECTIONS: Dict[str, str] = {
    "hw":     "practice/hw",
    "lab":    "practice/lab",
    "disc":   "study/disc",
    "slides": "study/slides",
}

# ============================================================
#  Dataclasses
# ============================================================

@dataclass
class MoveOp:
    source_abs: str   # absolute path of source file or directory
    dest_abs: str     # absolute path of destination
    reason: str       # human-readable explanation from LLM
    is_dir: bool = False  # True when moving an entire directory


@dataclass
class ExecutionStats:
    moved: int = 0
    skipped: int = 0
    errors: int = 0
    error_details: List[str] = field(default_factory=list)


@dataclass
class SectionPlan:
    label: str           # e.g. "hw", "disc"
    directory: str       # absolute path of section directory
    ops: List[MoveOp] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    scheme_summary: str = ""  # LLM's description of the numbering scheme
    folder_prefix: str = ""   # LLM-identified prefix (e.g. "hw", "slides")
    llm_debug: Optional[dict] = None  # raw LLM input/output for debugging


@dataclass
class OrganizerConfig:
    reorganized_root: str
    sections: Dict[str, str]   # label -> relative path
    db_path: str
    model: str = "gpt-5-mini-2025-08-07"
    dry_run: bool = True
    report_path: Optional[str] = None


class FilePlacement(BaseModel):
    source_name: str = Field(
        ...,
        description="Exact name of the file or directory to move (just the basename, no path)."
    )
    target_folder: str = Field(
        ...,
        description=(
            "The numbered subfolder to move this item into "
            "(e.g. 'hw01', 'disc02', 'slides17'). "
            "Must be a folder name that lives directly inside the section directory."
        )
    )
    reason: str = Field(
        ..., min_length=5,
        description="Brief explanation of why this item goes into that numbered folder."
    )
    is_directory: bool = Field(
        False,
        description="True if source_name is a directory (e.g. sol-hw01/, sol-disc02/)."
    )


class DirectoryReorgPlan(BaseModel):
    folder_prefix: str = Field(
        ...,
        description=(
            "The canonical prefix for numbered subfolders in this directory "
            "(e.g. 'hw', 'lab', 'disc', 'slides')."
        )
    )
    scheme_summary: str = Field(
        ...,
        description="One sentence describing the numbering scheme used."
    )
    placements: List[FilePlacement] = Field(
        default_factory=list,
        description=(
            "List of items that need to move. "
            "Omit items that are already correctly placed inside their numbered subfolder."
        )
    )


def load_descriptions(db_path: str, filenames: List[str]) -> Dict[str, str]:
    """
    Bulk-load file descriptions from the SQLite DB by filename.
    Returns a dict mapping filename -> description (empty string if not found).
    """
    if not db_path or not os.path.exists(db_path):
        return {}
    if not filenames:
        return {}

    result: Dict[str, str] = {}
    conn: Optional[sqlite3.Connection] = None

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        placeholders = ",".join("?" * len(filenames))
        cur.execute(
            f"SELECT file_name, description FROM file WHERE file_name IN ({placeholders})",
            filenames,
        )
        for row in cur.fetchall():
            fname = row["file_name"] or ""
            desc = row["description"] or ""
            if fname:
                result[fname] = desc

    except sqlite3.OperationalError as e:
        logger.warning("DB lookup failed: %s", e)

    finally:
        if conn:
            conn.close()

    return result


# ============================================================
#  System prompt
# ============================================================

SYSTEM_PROMPT = """\
You are a course file organizer. Given a directory listing with file/folder names \
and their descriptions, plan how to reorganize the contents into a clean \
numbered-subfolder hierarchy.

Rules:
1. Identify the numbering scheme for this directory (e.g. hw01–hw06, disc00–disc12, \
slides01–slides24). Use the file names and descriptions to determine the scheme.
2. Assign each LOOSE file or MISPLACED folder to the correct numbered subfolder.
   - "Loose" means the item is directly inside the section directory, not already \
inside a numbered subfolder.
   - A correctly-named numbered subfolder (e.g. disc01/) that already contains \
its own files is already correctly placed — OMIT it from placements.
3. Solution folders (e.g. sol-hw01/, sol-disc02/) belong INSIDE their corresponding \
numbered folder (e.g. hw01/sol-hw01/, disc02/sol-disc02/).
4. target_folder must be a numbered folder name using zero-padded two-digit numbers \
(e.g. "hw01", "disc02", "slides17", "lab00").
5. Reason carefully, then decide. Return ONLY items that need to move.
"""


def plan_directory(
    directory: str,
    db_path: str,
    model: str,
    client: OpenAI,
    debug_log: List[dict],
) -> SectionPlan:
    """
    Scan a section directory and use one LLM call to plan reorganization
    into a numbered-subfolder hierarchy.
    """
    label = os.path.basename(directory)
    plan = SectionPlan(label=label, directory=directory)

    # 1) Scan direct children (no recursion)
    if not os.path.isdir(directory):
        plan.warnings.append(f"Directory not found: {directory}")
        return plan

    try:
        entries = sorted(os.scandir(directory), key=lambda e: e.name)
    except PermissionError as e:
        plan.warnings.append(f"Cannot scan directory: {e}")
        return plan

    if not entries:
        plan.warnings.append("Directory is empty — nothing to do.")
        return plan

    # 2) Collect names for DB lookup (files only)
    file_entries = [e for e in entries if e.is_file() and not e.name.startswith(".")]
    dir_entries  = [e for e in entries if e.is_dir()  and not e.name.startswith(".")]

    file_names = [e.name for e in file_entries]
    descriptions = load_descriptions(db_path, file_names)

    # 3) Build user prompt
    lines = [f"Directory: {directory}\n", "Contents:\n"]
    for e in file_entries:
        desc = descriptions.get(e.name, "")
        desc_str = f" | DB: {desc[:200]}" if desc else ""
        lines.append(f'  - "{e.name}" | file{desc_str}')
    for e in dir_entries:
        lines.append(f'  - "{e.name}/" | directory')

    lines.append(
        "\nReturn a plan: for each item that needs to move, specify its target_folder. "
        "Items already correctly inside a numbered subfolder should be OMITTED."
    )
    user_prompt = "\n".join(lines)

    # 4) LLM call
    try:
        resp = client.responses.parse(
            model=model,
            instructions=SYSTEM_PROMPT,
            input=[{"role": "user", "content": user_prompt}],
            text_format=DirectoryReorgPlan,
        )
        reorg: DirectoryReorgPlan = resp.output_parsed

        debug_entry = {
            "directory": directory,
            "system_prompt": SYSTEM_PROMPT,
            "user_prompt": user_prompt,
            "raw_response": str(resp),
            "parsed": reorg.model_dump(),
        }
        debug_log.append(debug_entry)
        plan.llm_debug = debug_entry

    except Exception as e:
        warning = f"LLM call failed for {directory}: {e}"
        logger.error(warning)
        plan.warnings.append(warning)
        debug_log.append({"directory": directory, "error": str(e)})
        return plan

    plan.folder_prefix = reorg.folder_prefix
    plan.scheme_summary = reorg.scheme_summary

    # 5) Convert placements to MoveOp list
    for placement in reorg.placements:
        src_name = placement.source_name.strip().rstrip("/")
        tgt_name = placement.target_folder.strip().rstrip("/")

        # Defensive: if LLM tries to move a group folder into itself, ignore
        if src_name == tgt_name:
            continue

        source_abs = os.path.join(directory, src_name)
        dest_abs   = os.path.join(directory, tgt_name, src_name)

        # Validate source exists
        if not os.path.exists(source_abs):
            plan.warnings.append(
                f"LLM referenced non-existent source: {placement.source_name!r} "
                f"(target: {placement.target_folder})"
            )
            continue

        # Skip if already in the right place
        if os.path.abspath(source_abs) == os.path.abspath(dest_abs):
            continue

        plan.ops.append(MoveOp(
            source_abs=source_abs,
            dest_abs=dest_abs,
            reason=placement.reason,
            is_dir=placement.is_directory,
        ))

    return plan


# ============================================================
#  Directory unwrap/merge helpers (NO regex)
# ============================================================

def _list_non_hidden_children(dir_path: str) -> List[os.DirEntry]:
    """List non-hidden children of a directory (ignore dotfiles)."""
    children: List[os.DirEntry] = []
    with os.scandir(dir_path) as it:
        for e in it:
            if e.name.startswith("."):
                continue
            children.append(e)
    return children


def _unwrap_same_name_single_child_dir(src_dir: str) -> Optional[str]:
    """
    If src_dir contains exactly one child directory and its name equals src_dir basename,
    return that child directory path (meaning src_dir is a wrapper). Otherwise None.
    """
    if not os.path.isdir(src_dir):
        return None

    base = os.path.basename(os.path.abspath(src_dir))
    try:
        children = _list_non_hidden_children(src_dir)
    except Exception:
        return None

    if len(children) != 1:
        return None

    only = children[0]
    if only.is_dir() and only.name == base:
        return only.path

    return None


def _pick_non_conflicting_name(dst_dir: str, name: str) -> str:
    """If dst_dir/name exists, generate name__dupN to avoid overwrite."""
    candidate = os.path.join(dst_dir, name)
    if not os.path.exists(candidate):
        return candidate

    stem, ext = os.path.splitext(name)
    for i in range(1, 1000):
        new_name = f"{stem}__dup{i}{ext}" if ext else f"{stem}__dup{i}"
        candidate = os.path.join(dst_dir, new_name)
        if not os.path.exists(candidate):
            return candidate

    # Worst case fallback
    return os.path.join(dst_dir, f"{name}__dup9999")


def _merge_dir_contents(src_dir: str, dst_dir: str, dry_run: bool) -> None:
    """
    Move all direct children of src_dir into dst_dir (merge), without nesting src_dir itself.
    """
    if dry_run:
        print(f"  [DIR] [DRY-RUN MERGE] {src_dir} -> {dst_dir}")
        return

    os.makedirs(dst_dir, exist_ok=True)

    # Move each child into dst_dir (avoid collisions)
    for e in sorted(os.scandir(src_dir), key=lambda x: x.name):
        if e.name.startswith("."):
            continue
        src_child = e.path
        dst_child = _pick_non_conflicting_name(dst_dir, e.name)
        shutil.move(src_child, dst_child)

    # Remove empty src_dir
    try:
        os.rmdir(src_dir)
    except OSError:
        pass


# ============================================================
#  Move Executor (FIXED unwrap behavior)
# ============================================================

class MoveExecutor:
    def execute(self, ops: List[MoveOp], dry_run: bool = True) -> ExecutionStats:
        stats = ExecutionStats()
        for op in ops:
            try:
                self._execute_one(op, dry_run, stats)
            except Exception as e:
                stats.errors += 1
                msg = f"{op.source_abs} -> {op.dest_abs}: {e}"
                stats.error_details.append(msg)
                logger.error("Move failed: %s", msg)
        return stats

    def _execute_one(self, op: MoveOp, dry_run: bool, stats: ExecutionStats) -> None:
        src = op.source_abs
        dst = op.dest_abs
        tag = "[DIR]" if op.is_dir else "[FILE]"

        if not os.path.exists(src):
            print(f"  {tag} [SKIP — source missing] {src}")
            stats.skipped += 1
            return

        # For files: if dest exists, skip (safe)
        # For dirs: if dest exists, we will MERGE (do not skip)
        if os.path.exists(dst) and (not op.is_dir):
            print(f"  {tag} [SKIP — dest exists]   {dst}")
            stats.skipped += 1
            return

        if dry_run:
            print(f"  {tag} [DRY-RUN] {os.path.basename(src)}")
            print(f"         -> {dst}")
            print(f"         ({op.reason})")
            stats.moved += 1
            return

        parent = os.path.dirname(dst)
        os.makedirs(parent, exist_ok=True)

        # -------------------------
        # Directory move with unwrap
        # -------------------------
        if op.is_dir:
            # Ensure destination directory exists (for merge/unwrap)
            os.makedirs(dst, exist_ok=True)

            # Check if src_dir is a wrapper: src_dir/{same_name}/...
            inner = _unwrap_same_name_single_child_dir(src)
            if inner is not None:
                # Merge inner contents directly into dst
                _merge_dir_contents(inner, dst, dry_run=False)

                # Remove outer wrapper if empty
                try:
                    os.rmdir(src)
                except OSError:
                    pass

                print(f"  {tag} MOVED (UNWRAPPED) {os.path.basename(src)} -> {dst}")
                stats.moved += 1
                return

            # If destination already exists, merge src contents into dst
            if os.path.isdir(dst):
                _merge_dir_contents(src, dst, dry_run=False)
                print(f"  {tag} MOVED (MERGED) {os.path.basename(src)} -> {dst}")
                stats.moved += 1
                return

            # Fallback (shouldn't happen often): move directory normally
            shutil.move(src, dst)
            print(f"  {tag} MOVED  {os.path.basename(src)} -> {dst}")
            stats.moved += 1
            return

        # -------------------------
        # File move (normal)
        # -------------------------
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        print(f"  {tag} MOVED  {os.path.basename(src)} -> {dst}")
        stats.moved += 1



class SecondPassOrganizer:
    def __init__(self, config: OrganizerConfig):
        self.config = config
        self.client = OpenAI()
        self.executor = MoveExecutor()
        self.debug_log: List[dict] = []

    def run(self) -> Dict[str, SectionPlan]:
        plans: Dict[str, SectionPlan] = {}

        for label, rel_path in self.config.sections.items():
            base_dir = os.path.join(self.config.reorganized_root, rel_path)
            sep = "=" * 60
            print(f"\n{sep}")
            print(f"Section: {label.upper()}  ({base_dir})")
            print(sep)

            plan = plan_directory(
                directory=base_dir,
                db_path=self.config.db_path,
                model=self.config.model,
                client=self.client,
                debug_log=self.debug_log,
            )
            plans[label] = plan

            if plan.scheme_summary:
                print(f"  Scheme: {plan.scheme_summary}")
            if plan.warnings:
                for w in plan.warnings:
                    print(f"  WARNING: {w}")

            if not plan.ops:
                print("  No operations needed.")
            else:
                print(f"  {len(plan.ops)} operation(s) planned:")
                stats = self.executor.execute(plan.ops, dry_run=self.config.dry_run)
                print(
                    f"  [{label}] moved={stats.moved}  "
                    f"skipped={stats.skipped}  errors={stats.errors}"
                )
                for detail in stats.error_details:
                    print(f"  ERROR: {detail}")

        # Save LLM debug log
        debug_path = "second_pass_llm_debug.json"
        with open(debug_path, "w", encoding="utf-8") as f:
            json.dump(self.debug_log, f, ensure_ascii=False, indent=2)
        print(f"\nLLM debug log -> {debug_path}")

        # Save optional markdown report
        if self.config.report_path:
            self._save_report(plans)

        return plans

    def _save_report(self, plans: Dict[str, SectionPlan]) -> None:
        lines = [
            "# Second Pass Organizer Report\n\n",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  \n",
            f"Mode: {'DRY-RUN' if self.config.dry_run else 'EXECUTE'}  \n\n",
        ]
        for label, plan in plans.items():
            lines.append(f"## {label.upper()} — `{plan.directory}`\n\n")
            if plan.scheme_summary:
                lines.append(f"**Scheme:** {plan.scheme_summary}  \n\n")
            if plan.warnings:
                lines.append(f"**Warnings ({len(plan.warnings)}):**\n")
                for w in plan.warnings:
                    lines.append(f"- {w}\n")
                lines.append("\n")
            lines.append(f"**Operations ({len(plan.ops)}):**\n")
            for op in plan.ops:
                tag = "DIR " if op.is_dir else "FILE"
                src_rel = os.path.relpath(op.source_abs, self.config.reorganized_root)
                dst_rel = os.path.relpath(op.dest_abs, self.config.reorganized_root)
                lines.append(f"- `[{tag}]` `{src_rel}` → `{dst_rel}`  \n")
                lines.append(f"  _{op.reason}_\n")
            lines.append("\n")

        with open(self.config.report_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        print(f"Report -> {self.config.report_path}")



def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description=(
            "Second-pass organizer: restructures BFS output into a "
            "numbered-subfolder hierarchy using LLM reasoning."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--root", "-r", required=True,
        help="Root of the reorganized output directory (e.g. ./61A_reorganized_v4)",
    )
    parser.add_argument(
        "--db", "-d", default="file.db",
        help="Path to the SQLite file.db for file descriptions (default: file.db)",
    )
    parser.add_argument(
        "--model", default="gpt-5-mini-2025-08-07",
        help="OpenAI model for LLM reasoning (default: gpt-5-mini-2025-08-07)",
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="Actually move files. Default is dry-run (print plan only).",
    )
    parser.add_argument(
        "--sections", nargs="+",
        choices=list(DEFAULT_SECTIONS.keys()),
        default=list(DEFAULT_SECTIONS.keys()),
        help="Which sections to process (default: all four: hw lab disc slides)",
    )
    parser.add_argument(
        "--section-paths", nargs="+", metavar="LABEL=REL_PATH",
        help=(
            "Add or override section paths. "
            "Format: label=relative/path (e.g. 'extra=study/extra')."
        ),
    )
    parser.add_argument(
        "--report", default=None, metavar="PATH",
        help="Save a markdown report to this path.",
    )

    args = parser.parse_args()

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print(f"Error: directory not found: {root}", file=sys.stderr)
        sys.exit(1)

    if not os.environ.get("OPENAI_API_KEY"):
        print("Error: OPENAI_API_KEY environment variable not set.", file=sys.stderr)
        sys.exit(1)

    # Build sections dict
    sections: Dict[str, str] = {k: DEFAULT_SECTIONS[k] for k in args.sections}
    if args.section_paths:
        for entry in args.section_paths:
            if "=" not in entry:
                print(f"Error: --section-paths entry must be label=path, got: {entry!r}", file=sys.stderr)
                sys.exit(1)
            label, rel_path = entry.split("=", 1)
            sections[label.strip()] = rel_path.strip()

    dry_run = not args.execute

    print("=" * 60)
    print("Second Pass Organizer")
    print("=" * 60)
    print(f"Root:    {root}")
    print(f"DB:      {args.db}")
    print(f"Model:   {args.model}")
    print(f"Sections: {', '.join(f'{k}={v}' for k, v in sections.items())}")
    print(f"Mode:    {'DRY-RUN (use --execute to apply)' if dry_run else 'EXECUTE — files will be moved!'}")
    print("=" * 60)

    config = OrganizerConfig(
        reorganized_root=root,
        sections=sections,
        db_path=os.path.abspath(args.db),
        model=args.model,
        dry_run=dry_run,
        report_path=args.report,
    )

    organizer = SecondPassOrganizer(config)
    plans = organizer.run()

    # Final summary
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    total_ops = 0
    total_warnings = 0
    for label, plan in plans.items():
        n_ops = len(plan.ops)
        n_warn = len(plan.warnings)
        total_ops += n_ops
        total_warnings += n_warn
        print(f"  {label:<10} {n_ops:3d} ops  {n_warn:2d} warnings")
    print(f"  {'TOTAL':<10} {total_ops:3d} ops  {total_warnings:2d} warnings")
    if dry_run:
        print("\nDry-run complete. Re-run with --execute to apply changes.")


if __name__ == "__main__":
    main()