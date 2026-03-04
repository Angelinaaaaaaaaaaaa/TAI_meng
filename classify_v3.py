#!/usr/bin/env python3
"""
classify_v3.py

LLM-based Classifier for course folders and files — v3.

Notes (per your requirements):
  - Prompts are unchanged in content (only minor grammar/spacing fixes).
  - No prompt caps: folder prompt includes ALL files + ALL concatenated descriptions.
  - File classification is forced to ONLY: study / practice / support in outputs.
    (If the model returns "skip", we map it to "support" to preserve a 3-way system.)
"""

import json
import logging
import os
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from openai import OpenAI
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

LLM_DEBUG_LOG_FILE = "bfs_v3_llm_debug.json"


class Category(str, Enum):
    STUDY = "study"
    PRACTICE = "practice"
    SUPPORT = "support"
    SKIP = "skip"


# ====================================================================
#  Shared Data Structures
# ====================================================================

from dataclasses import dataclass, field
from collections import deque


@dataclass
class FileMeta:
    """Metadata for a single file."""
    source_path: str
    folder_path: str
    file_name: str
    description: Optional[str] = None
    file_hash: Optional[str] = None


@dataclass
class FolderNode:
    """Tree node representing a folder."""
    path: str
    name: str
    files: List[FileMeta] = field(default_factory=list)
    children: Dict[str, "FolderNode"] = field(default_factory=dict)

    def is_leaf(self) -> bool:
        return len(self.children) == 0

    def total_file_count(self) -> int:
        count = len(self.files)
        for child in self.children.values():
            count += child.total_file_count()
        return count


@dataclass
class FileIndexEntry:
    """One row from the DB file table."""
    uuid: str
    file_name: str
    description: str
    original_path: Optional[str] = None
    relative_path: Optional[str] = None
    extra_info: Optional[str] = None
    file_hash: Optional[str] = None


@dataclass
class FolderStats:
    """Structural statistics for a folder."""
    total_file_count: int
    immediate_file_count: int
    subfolder_count: int
    subfolder_names: List[str]
    extension_counts: Dict[str, int]
    is_homogeneous: bool
    primary_extensions: List[str]


@dataclass
class ClassificationResult:
    """Result of classifying a folder or file."""
    category: Category
    reason: str
    is_mixed: bool = False
    folder_description: str = ""
    # Folder organisation scheme (folders only; both False for files/SKIP/mixed)
    by_type: bool = False
    by_sequence: bool = False


# ====================================================================
#  Pydantic Models for LLM Structured Output
# ====================================================================

class LLMFolderDecision(BaseModel):
    """LLM output for a single folder: reason FIRST, then category."""
    folder_path: str = Field(..., description="Exact folder path from input")
    reason: str = Field(
        ..., min_length=10,
        description=(
            "MUST be filled FIRST. Short reasoning about what this folder "
            "contains and why it belongs in the chosen category."
        ),
    )
    category: str = Field(
        ..., pattern="^(practice|study|support|skip)$",
        description="Category decided AFTER reasoning.",
    )
    is_mixed: bool = Field(
        False,
        description=(
            "True if the folder contains files from MULTIPLE (>1) categories. "
            "When true, category is automatically overridden to 'skip'."
        ),
    )
    folder_description: str = Field(
        "",
        description="One-sentence summary of the folder's pedagogical purpose.",
    )
    by_type: bool = Field(
        False,
        description=(
            "True if this folder's direct children are organised by task/media TYPE — "
            "i.e., different kinds of material sit side-by-side as siblings. "
            "Example: practice/ contains hw/, lab/, proj/ under practice. "
            "Set False when children are sequential or when is_mixed is True."
        ),
    )
    by_sequence: bool = Field(
        False,
        description=(
            "True if this folder's direct children are organised by topic/time SEQUENCE — "
            "i.e., siblings share the same type but differ by number/topic. "
            "Examples: lecture01/, lecture02/, … or disc01/, disc02/, … "
            "or proj/ants, proj/cats (topic-ordered projects). "
            "Set False when children are type-grouped or when is_mixed is True."
        ),
    )


class LLMFileDecision(BaseModel):
    """LLM output for a single file classification."""
    file_path: str = Field(..., description="Exact file path from input")
    reason: str = Field(
        ..., min_length=5,
        description="Reasoning about this file's educational purpose.",
    )
    # Allow skip in parsing to avoid hard failures, but we force output to 3-way later.
    category: str = Field(..., pattern="^(practice|study|support|skip)$")


# ====================================================================
#  Helper: collect all files under a FolderNode
# ====================================================================

def collect_all_files(node: FolderNode) -> List[FileMeta]:
    """Recursively collect all files under a FolderNode (BFS order)."""
    files: List[FileMeta] = []
    queue: deque[FolderNode] = deque([node])
    while queue:
        cur = queue.popleft()
        files.extend(cur.files)
        for child in cur.children.values():
            queue.append(child)
    return files


# ====================================================================
#  LLMClassifier
# ====================================================================

class LLMClassifier:
    """
    LLM-based classifier for course folders and files.

    Consolidates reasoning + decision into a single LLM call per item.
    """

    def __init__(self, model: str = "gpt-5-mini-2025-08-07"):
        if not os.environ.get("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY environment variable not set")
        self.model = model
        self.client = OpenAI()
        self.debug_log: List[dict] = []

    # -------------------- Folder Classification -------------------- #

    def classify_folder(
        self,
        node: FolderNode,
        file_index: Dict[str, FileIndexEntry],
        folder_stats: FolderStats,
        concat_desc: str,
        ancestor_descriptions: Optional[List[str]] = None,
    ) -> ClassificationResult:
        """
        Classify a folder in ONE LLM call: reason + decide + summarize.

        Returns ClassificationResult with category, reason, is_mixed,
        folder_description, by_type, by_sequence.
        Mixed folders get category=SKIP and by_type=by_sequence=False.
        """
        _ = file_index  # reserved for subclass overrides
        system_prompt = self._folder_system_prompt()
        user_prompt = self._folder_user_prompt(
            node, folder_stats, concat_desc, ancestor_descriptions
        )

        try:
            resp = self.client.responses.parse(
                model=self.model,
                instructions=system_prompt,
                input=[{"role": "user", "content": user_prompt}],
                text_format=LLMFolderDecision,
            )
            decision: LLMFolderDecision = resp.output_parsed

            self._log_call(
                "folder_classification",
                system_prompt, user_prompt,
                raw=str(resp),
                parsed=decision.model_dump(),
            )

            # Mixed → force SKIP and clear the organisation flags
            if decision.is_mixed:
                return ClassificationResult(
                    category=Category.SKIP,
                    reason=decision.reason,
                    is_mixed=True,
                    folder_description=decision.folder_description,
                    by_type=False,
                    by_sequence=False,
                )

            return ClassificationResult(
                category=Category(decision.category),
                reason=decision.reason,
                is_mixed=False,
                folder_description=decision.folder_description,
                by_type=decision.by_type,
                by_sequence=decision.by_sequence,
            )

        except Exception as e:
            self._log_call(
                "folder_classification",
                system_prompt, user_prompt,
                error=str(e),
            )
            raise

    # -------------------- File Classification -------------------- #

    def classify_file(
        self,
        file_meta: FileMeta,
        file_index: Dict[str, FileIndexEntry],
        ancestor_descriptions: Optional[List[str]] = None,
        sibling_names: Optional[List[str]] = None,
    ) -> ClassificationResult:
        """Classify an individual file."""
        db_entry = file_index.get(file_meta.file_name)
        file_desc = ""
        if db_entry and db_entry.description:
            file_desc = db_entry.description
        elif file_meta.description:
            file_desc = file_meta.description

        system_prompt = self._file_system_prompt()
        user_prompt = self._file_user_prompt(
            file_meta, file_desc, ancestor_descriptions, sibling_names
        )

        try:
            resp = self.client.responses.parse(
                model=self.model,
                instructions=system_prompt,
                input=[{"role": "user", "content": user_prompt}],
                text_format=LLMFileDecision,
            )
            decision: LLMFileDecision = resp.output_parsed

            self._log_call(
                "file_classification",
                system_prompt, user_prompt,
                raw=str(resp),
                parsed=decision.model_dump(),
            )

            # Force file-level output to ONLY: study / practice / support
            cat = decision.category
            if cat == "skip":
                cat = "support"

            return ClassificationResult(
                category=Category(cat),
                reason=decision.reason,
            )

        except Exception as e:
            self._log_call(
                "file_classification",
                system_prompt, user_prompt,
                error=str(e),
            )
            raise

    # -------------------- Batch File Classification -------------------- #

    def classify_files(
        self,
        files: List[FileMeta],
        file_index: Dict[str, FileIndexEntry],
        ancestor_descriptions: Optional[List[str]] = None,
    ) -> Dict[str, ClassificationResult]:
        """Classify a list of files one-by-one (sibling context included)."""
        results: Dict[str, ClassificationResult] = {}
        if not files:
            return results

        dir_to_names: Dict[str, List[str]] = {}
        for f in files:
            dir_to_names.setdefault(f.folder_path, []).append(f.file_name)

        total = len(files)
        for i, file_meta in enumerate(files, 1):
            logger.info(
                f"[classify_files] ({i}/{total}) Classifying: {file_meta.source_path}"
            )
            siblings = [
                name for name in dir_to_names.get(file_meta.folder_path, [])
                if name != file_meta.file_name
            ]
            result = self.classify_file(
                file_meta, file_index,
                ancestor_descriptions=ancestor_descriptions,
                sibling_names=siblings if siblings else None,
            )
            results[file_meta.source_path] = result

        logger.info(f"[classify_files] Completed: {total} files classified")
        return results

    # -------------------- Prompts -------------------- #
    # PROMPTS: content unchanged (only minimal grammar/spacing fixes).

    def _folder_system_prompt(self) -> str:
        """System prompt for folder classification."""
        return (
            "You are classifying course folders into four categories:\n"
            "- practice: Students DO or PRODUCE work — homework (hw), labs, projects.\n"
            "  This also includes exam solutions/explanations\n"
            "- study: Instructional learning content that students READ, WATCH, or REVIEW.\n"
            "  This includes: lectures, lecture slides/notes/PDFs, discussion/section materials.\n"
            "- support: Global course support like syllabus, textbooks, tools/how-to docs.\n"
            "  Study guides belongs to support!\n"
            "- skip: Folders that contain files from MULTIPLE (>1) categories or are not useful .\n\n"
            "Rules:\n"
            "1. You receive a description of a folder with its structure, file listings,\n"
            "   and concatenated file descriptions from the database.\n"
            "2. You may also receive ancestor descriptions providing hierarchical context.\n"
            "3. Your primary goal is to assign ONE best category based on overall purpose.\n"
            "4. Think top-down: classify the folder as a whole, not per-file.\n"
            "5. CRITICAL — you MUST reason FIRST, then decide:\n"
            "   a. Write a short 'reason' field explaining why it fits a particular category.\n"
            "   b. Only AFTER writing the reason, fill in 'category'.\n"
            "   c. The reason must logically support the chosen category.\n"
            "   DO NOT pick a category first and then justify it.\n"
            "6. Set is_mixed=true if the folder contains a clear mix of categories\n"
            "   (e.g., both homework (practice) and lecture slides (study)). is_mixed forces category to skip.\n"
            "7. Write a brief folder_description (one sentence) summarizing the\n"
            "   folder's pedagogical purpose. This will be used as context for\n"
            "   classifying child folders/files.\n"
            "8. folder_path MUST match exactly the string shown after 'Folder:' in input.\n"
            "9. Set by_type=true if this folder's immediate children are organised by\n"
            "   TASK/MEDIA TYPE — different kinds of material side-by-side.\n"
            "   Example: practice/ has hw/, lab/, proj/ (three different types).\n"
            "   Example: study/ has slides/, videos/, readings/ (three media types).\n"
            "10. Set by_sequence=true if this folder's immediate children are organised\n"
            "    by TOPIC/TIME SEQUENCE — same kind of content, numbered or topic-ordered.\n"
            "    Example: study/ has lecture01/, lecture02/ (sequential lectures).\n"
            "    by_type and by_sequence are mutually exclusive; both must be False\n"
            "    when is_mixed=true or the organisation scheme is unclear.\n"
        )

    def _folder_user_prompt(
        self,
        node: FolderNode,
        stats: FolderStats,
        concat_desc: str,
        ancestor_descriptions: Optional[List[str]] = None,
    ) -> str:
        """Build the user prompt for classifying one folder."""
        lines: List[str] = []

        if ancestor_descriptions:
            lines.append("Ancestor context (root -> parent):")
            for i, desc in enumerate(ancestor_descriptions):
                lines.append(f"  [{i}] {desc}")
            lines.append("")

        lines.append(f"Folder: {node.path}")
        lines.append(f"Name: {node.name}")
        lines.append(f"TotalFiles: {stats.total_file_count}")
        lines.append(f"ImmediateFiles: {stats.immediate_file_count}")
        lines.append(f"SubfolderCount: {stats.subfolder_count}")
        lines.append(f"HasSubfolders: {'yes' if stats.subfolder_count > 0 else 'no'}")
        lines.append(f"FileTypesHomogeneous: {'yes' if stats.is_homogeneous else 'no'}")
        lines.append(
            f"PrimaryFileTypes: {', '.join(stats.primary_extensions) or 'N/A'}"
        )

        if stats.subfolder_names:
            shown = stats.subfolder_names[:30]
            lines.append("Subfolders (immediate):")
            lines.append("  " + ", ".join(shown))
            if len(stats.subfolder_names) > 30:
                lines.append(f"  ... and {len(stats.subfolder_names) - 30} more")

        all_files = collect_all_files(node)
        if all_files:
            lines.append("\nFiles (name + description):")
            for f in all_files:
                desc = (f.description or "").replace("\n", " ").strip()
                if not desc:
                    desc = "[no description]"
                lines.append(f"  - {f.file_name} :: {desc}")

        if concat_desc:
            lines.append("\nConcatenated file descriptions:")
            lines.append(concat_desc)

        lines.append(
            "\nClassify this folder. Write reason FIRST, then category, "
            "then set is_mixed, by_type, by_sequence."
        )

        return "\n".join(lines)

    def _file_system_prompt(self) -> str:
        """System prompt for individual file classification."""
        return (
            "You are classifying a single course file into one of three categories:\n\n"
            "- study: Learning materials that students READ, WATCH, or REVIEW.\n"
            "  e.x. lecture slides/videos/readings, lecture notes,\n"
            "  discussion materials, supplement code files for demonstration\n"
            "  These materials usually focus on specific course concepts.\n\n"
            "- practice: Student-PRODUCED work and assignments.\n"
            "  e.x. homework, labs, projects, exercises, quizzes, exam papers.\n\n"
            "- support: Course LOGISTICS and SUPPLEMENTARY resources.\n"
            "  e.x. syllabus, calendar, study guides, staff information.\n\n"
            "  Ignore files that are meaningless.\n\n"
            "Key distinctions:\n"
            "  'practice' = student-produced assignments.\n"
            "  'study'    = instructor-provided, lecture-related learning material.\n"
            "  Discussion worksheets are study material even when they contain\n"
            "  problems, because they are part of instructor-led section flow.\n\n"
            "  NOTE: study guide realted info belongs to practice even if it contains study in its name."
            "Rules:\n"
            "1. You receive the file's name, path, description from the database,\n"
            "   and context from ancestor folders.\n"
            "2. You may also receive a list of sibling files in the same directory.\n"
            "   Files in the same folder with similar naming conventions usually\n"
            "   belong to the same category — use this as a strong signal.\n"
            "3. Reason FIRST about what this file is and its educational purpose.\n"
            "4. Then decide on category.\n"
            "5. file_path MUST match exactly the path shown in the input.\n"
        )

    def _file_user_prompt(
        self,
        file_meta: FileMeta,
        file_desc: str,
        ancestor_descriptions: Optional[List[str]] = None,
        sibling_names: Optional[List[str]] = None,
    ) -> str:
        """Build user prompt for classifying one file."""
        lines: List[str] = []

        if ancestor_descriptions:
            lines.append("Ancestor context (root -> parent):")
            for i, desc in enumerate(ancestor_descriptions):
                lines.append(f"  [{i}] {desc}")
            lines.append("")

        lines.append(f"File: {file_meta.source_path}")
        lines.append(f"Name: {file_meta.file_name}")
        lines.append(f"Parent folder: {file_meta.folder_path}")

        ext = os.path.splitext(file_meta.file_name)[1].lower()
        if ext:
            lines.append(f"Extension: {ext}")

        if file_desc:
            lines.append(f"Description: {file_desc}")
        else:
            lines.append("Description: [none available]")

        if sibling_names:
            shown = sibling_names[:20]
            lines.append(f"\nSibling files in same directory ({len(sibling_names)} total):")
            for name in shown:
                lines.append(f"  - {name}")
            if len(sibling_names) > 20:
                lines.append(f"  ... and {len(sibling_names) - 20} more")

        lines.append(
            "\nClassify this file. Write reason FIRST, then category."
        )

        return "\n".join(lines)

    # -------------------- Debug Logging -------------------- #

    def _log_call(
        self,
        call_type: str,
        system_prompt: str,
        user_prompt: str,
        raw: Optional[str] = None,
        parsed: Optional[dict] = None,
        error: Optional[str] = None,
    ) -> None:
        self.debug_log.append({
            "timestamp": datetime.now().isoformat(),
            "call_type": call_type,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "raw_output": raw,
            "parsed_output": parsed,
            "error": error,
        })
        status = "OK" if not error else f"ERROR: {error}"
        logger.info(f"[LLM] {call_type} — {status}")

    def save_debug_log(self, path: str = LLM_DEBUG_LOG_FILE) -> None:
        if not self.debug_log:
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.debug_log, f, ensure_ascii=False, indent=2)
        logger.info(f"[LLM] Saved {len(self.debug_log)} debug entries to {path}")