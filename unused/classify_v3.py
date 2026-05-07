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
    # Task/sequence names derived structurally or by LLM re-categorization
    task_name: Optional[str] = None
    sequence_name: Optional[str] = None


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


class LLMTaskNameInference(BaseModel):
    """LLM output for inferring a task name for a file/folder missing one."""
    source_path: str = Field(..., description="Exact source path from input")
    task_name: str = Field(
        ...,
        description=(
            "The best matching task name from the provided known_task_names set. "
            "If none match well, choose the closest or propose a short new name "
            "like 'hw', 'lab', 'lecture', 'discussion', 'project', 'exam'."
        ),
    )
    reason: str = Field(..., min_length=5, description="Why this task name fits.")


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

    # -------------------- Task Name Inference -------------------- #

    def infer_task_name(
        self,
        source_path: str,
        category: str,
        description: str,
        known_task_names: List[str],
        ancestor_descriptions: Optional[List[str]] = None,
    ) -> Optional[str]:
        """
        Given a file/folder with no identified task name, infer the best
        matching task name from the provided set of known task names.
        Returns the inferred task_name string, or None on failure.
        """
        known_sorted = sorted(known_task_names)
        known_str = ", ".join(f'"{n}"' for n in known_sorted) if known_sorted else "(none known yet)"

        system_prompt = (
            "You are helping to organize course files by assigning each file a task name.\n"
            "A task name identifies the type of educational task the file belongs to, "
            "e.g. 'hw', 'lab', 'proj', 'lecture', 'discussion', 'exam', 'quiz'.\n\n"
            "You will receive:\n"
            "  - The file/folder path\n"
            "  - Its category (study / practice / support)\n"
            "  - Its description (if available)\n"
            "  - Ancestor folder context\n"
            "  - A set of known task names already identified in this course\n\n"
            "Rules:\n"
            "1. Prefer matching a name from the known_task_names set when it fits.\n"
            "2. If none fit, propose a short lowercase task name (e.g. 'reading', 'quiz').\n"
            "3. Reason FIRST, then fill task_name.\n"
            "4. source_path MUST match exactly the path shown in the input.\n"
        )

        lines: List[str] = []
        if ancestor_descriptions:
            lines.append("Ancestor context (root -> parent):")
            for i, desc in enumerate(ancestor_descriptions):
                lines.append(f"  [{i}] {desc}")
            lines.append("")

        lines.append(f"Path: {source_path}")
        lines.append(f"Category: {category}")
        lines.append(f"Description: {description or '[none]'}")
        lines.append(f"Known task names: {known_str}")
        lines.append("\nInfer the task name. Reason FIRST, then fill task_name.")
        user_prompt = "\n".join(lines)

        try:
            resp = self.client.responses.parse(
                model=self.model,
                instructions=system_prompt,
                input=[{"role": "user", "content": user_prompt}],
                text_format=LLMTaskNameInference,
            )
            decision: LLMTaskNameInference = resp.output_parsed

            self._log_call(
                "task_name_inference",
                system_prompt, user_prompt,
                raw=str(resp),
                parsed=decision.model_dump(),
            )
            return decision.task_name

        except Exception as e:
            self._log_call(
                "task_name_inference",
                system_prompt, user_prompt,
                error=str(e),
            )
            logger.warning(f"[LLM] infer_task_name failed for {source_path}: {e}")
            return None

    # -------------------- Prompts -------------------- #
    # PROMPTS: content unchanged (only minimal grammar/spacing fixes).

    def _folder_system_prompt(self) -> str:
        """System prompt for folder classification."""
        return (
            "You are classifying course folders into four categories:\n"
            "- practice: Students DO or PRODUCE work — homework (hw), labs, projects, coding assignments.\n"
            "  This also includes assignment/exam solution sets and answer keys for those assignments/exams.\n"
            "- study: Instructor-provided learning content that students READ, WATCH, or REVIEW.\n"
            "  This may includes: lectures, lecture slides/notes, and discussion/section/tutorial.\n"
            "- support: Global course support and reference resources like syllabus, calendars, staff info, tools/how-to docs,\n"
            "  textbooks/readings/reference materials. Study guides belong to support!\n"
            "- skip: Folders that contain files from MULTIPLE (>1) categories or are not useful.\n\n"
            "Critical clarifications:\n"
            "A) Discussion/section materials are study (even if they contain problems).\n"
            "   If you see 'discussion', 'disc', 'section', 'tutorial', 'worksheet' folders, default to study.\n"
            "   Discussion solutions/answer keys that belong to discussion/section flow should still be study.\n"
            "B) Textbooks and readings are support, NOT study. Study guide folders/files always belong to support.\n"
            "C) Study should be lecture-aligned when possible.\n"
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
            "7. Write a brief folder_description (one sentence) summarizing the folder's pedagogical purpose.\n"
            "8. folder_path MUST match exactly the string shown after 'Folder:' in input.\n"
            "9. Set by_type=true if this folder's immediate children are organised by TASK/MEDIA TYPE.\n"
            "10. Set by_sequence=true if this folder's immediate children are organised by TOPIC/TIME SEQUENCE.\n"
            "    by_type and by_sequence are mutually exclusive; both must be False when is_mixed=true or unclear.\n"
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
            "- study: Instructor-provided learning materials that students READ, WATCH, or REVIEW.\n"
            "  Examples: lecture slides/videos/readings used for lecture delivery, lecture notes,\n"
            "  discussion/section/tutorial worksheets and handouts, and demo code used for teaching.\n\n"
            "- practice: Student-facing task and deliverable materials, including work students are expected to complete, submit, or follow.\n"
            "  Examples: homework, labs, projects, exercises, quizzes, exams, and their assignment/exam solution sets.\n\n"
            "- support: Course logistics and reference resources.\n"
            "  Examples: syllabus, calendar, policies, staff info, tooling/how-to docs, study guides,\n"
            "  and textbooks/readings/reference chapters (treat as support even if related to lecture topics).\n\n"
            "Key distinctions:\n"
            "  'practice' = students do/submit work (or solutions for those assignments).\n"
            "  'study'    = instructor-led teaching flow (lecture/discussion materials), even if it contains problems.\n"
            "  'support'  = logistics + reference (textbooks/readings/study guides).\n\n"
            "Hard rules:\n"
            "1) Discussion/section/tutorial/worksheet files are study by default.\n"
            "2) Textbook/readings/chapter/references are support (not study).\n"
            "3) Study guides are support (even if the filename contains 'study').\n\n"
            "Rules:\n"
            "1. You receive the file's name, path, description from the database,\n"
            "   and context from ancestor folders.\n"
            "2. You may also receive a list of sibling files in the same directory.\n"
            "   Maintain ORGANIZATIONAL CONSISTENCY: if files form a clear series or pedagogical unit\n"
            "   (e.g., lab1/lab2/lab3, proj1a/proj1b, disc01/disc02, hw1/hw2), prefer assigning the SAME\n"
            "   category to the whole series so the reorganized repository is intuitive to students.\n"
            "3. Favor grouping related materials for the same assignment/lab/project/discussion together,\n"
            "   even if one file is slightly more instructional and another is slightly more task-oriented.\n"
            "4. Only split same-series files across categories when one file is clearly a global logistics or\n"
            "   reference resource unrelated to the pedagogical unit.\n"
            "5. Reason FIRST about what this file is and its educational purpose.\n"
            "6. Then decide on category.\n"
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

        lines.append("\nReorganization preference:")
        lines.append("  - Keep same-series sibling files in one category when possible.")
        lines.append("  - Favor intuitive grouping over narrow semantic differences.")
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