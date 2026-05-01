#!/usr/bin/env python3
"""
classify_v4.py

LLM-based Classifier for course folders and files — v4.

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
from typing import Dict, List, Optional, Set

from openai import APITimeoutError, OpenAI
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

LLM_DEBUG_LOG_FILE = "bfs_v4_llm_debug.json"
DEFAULT_OPENAI_TIMEOUT_SECONDS = 45.0


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
    task_name: Optional[str] = None
    sequence_name: Optional[str] = None
    category_depth: int = 0
    by_type: bool = False      # children's names are the task_name
    by_sequence: bool = False  # this folder's name is task_name; children names are sequence_name


@dataclass
class TaskSequenceResult:
    """Result of extracting task/sequence metadata for a folder or file."""
    reason: str
    task_name: Optional[str] = None
    sequence_name: Optional[str] = None
    category_depth: int = 0
    by_type: bool = False      # children's names are the task_name
    by_sequence: bool = False  # this folder's name is task_name; children names are sequence_name


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


class LLMFolderTaskSequenceDecision(BaseModel):
    """LLM output for folder task/sequence metadata."""
    folder_path: str = Field(..., description="Exact folder path from input")
    reason: str = Field(
        ..., min_length=10,
        description="Reasoning about the folder's task and sequence metadata.",
    )
    task_name: str = Field(
        "",
        description="Short normalized task label inferred from the item context.",
    )
    seq_name: str = Field(
        "",
        description="Optional sequence label such as 01, 02, week03, or partA.",
    )
    category_depth: int = Field(
        0,
        ge=0,
        description="Deepest category-relative path level where task/sequence metadata appears.",
    )
    by_type: bool = Field(
        False,
        description=(
            "True if this folder's direct children are organised by task/media TYPE, "
            "so child names should be treated as task names."
        ),
    )


class LLMFileDecision(BaseModel):
    """LLM output for a single file category decision."""
    file_path: str = Field(..., description="Exact file path from input")
    reason: str = Field(
        ..., min_length=5,
        description="Reasoning about this file's educational purpose.",
    )
    # Allow skip in parsing to avoid hard failures, but we force output to 3-way later.
    category: str = Field(..., pattern="^(practice|study|support|skip)$")


class LLMFileTaskSequenceDecision(BaseModel):
    """LLM output for file task/sequence metadata."""
    file_path: str = Field(..., description="Exact file path from input")
    reason: str = Field(
        ..., min_length=5,
        description="Reasoning about this file's task and sequence metadata.",
    )
    task_name: str = Field(
        "",
        description="Short normalized task label inferred from the item context.",
    )
    seq_name: str = Field(
        "",
        description="Optional sequence label such as 01, 02, week03, or partA.",
    )
    category_depth: int = Field(
        0,
        ge=0,
        description="Deepest category-relative path level where task/sequence metadata appears.",
    )


class LLMTaskNameInference(BaseModel):
    """LLM output for inferring a task name from a known set."""
    source_path: str = Field(..., description="Exact path from input")
    reason: str = Field(..., min_length=5, description="Reasoning about which task name fits best.")
    task_name: str = Field("", description="Chosen or proposed task name (lowercase, short).")


class LLMSequenceBatchItem(BaseModel):
    """LLM output for one sequence-name decision in a batch."""
    source_path: str = Field(..., description="Exact path from input")
    reason: str = Field(..., min_length=5, description="Reasoning about the sequence marker.")
    seq_name: str = Field("", description="Sequence label, or blank if none is supported.")
    by_sequence: bool = Field(
        False,
        description=(
            "True for folder items whose direct children are organised by topic/time sequence."
        ),
    )
    category_depth: int = Field(
        0,
        ge=0,
        description="Deepest category-relative source path level where sequence metadata appears.",
    )


class LLMSequenceBatchDecision(BaseModel):
    """LLM output for batch sequence-name inference."""
    items: List[LLMSequenceBatchItem] = Field(
        default_factory=list,
        description="One sequence decision for each input item.",
    )


def _sanitize_task_name(raw: str) -> Optional[str]:
    """
    Post-process a task_name returned by the LLM.
    Returns the stripped lowercase value, or None if empty.
    """
    value = (raw or "").strip().lower()
    if not value:
        return None
    return value


def _category_depth(
    source_path: str,
    task_name: Optional[str],
    sequence_name: Optional[str],
    model_depth: int = 0,
) -> int:
    """Return deepest category-relative source path level for task/sequence metadata."""
    parts = [p for p in source_path.replace("\\", "/").split("/") if p]
    is_file = "." in os.path.basename(source_path)
    filename = os.path.basename(source_path) if is_file else ""
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


def _is_youtube_course_video(file_meta: FileMeta, file_desc: str = "") -> bool:
    """Detect YouTube/course-video metadata files that should be study material."""
    text = " ".join(
        part
        for part in [
            file_meta.source_path,
            file_meta.folder_path,
            file_meta.file_name,
            file_meta.description or "",
            file_desc or "",
        ]
        if part
    ).lower()
    return (
        "youtube" in text
        or "youtu.be" in text
        or "youtube.com" in text
    )


# ====================================================================
#  LLMClassifier
# ====================================================================

class LLMClassifier:
    """
    LLM-based classifier for course folders and files.

    Consolidates reasoning + decision into a single LLM call per item.
    """

    def __init__(
        self,
        model: str = "gpt-5-mini-2025-08-07",
        timeout_seconds: float = DEFAULT_OPENAI_TIMEOUT_SECONDS,
    ):
        if not os.environ.get("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY environment variable not set")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.client = OpenAI(timeout=timeout_seconds)
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
        and folder_description. Organization flags are inferred by task/sequence calls.
        Mixed folders get category=SKIP and organization flags default to False.
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
            )

        except Exception as e:
            self._log_call(
                "folder_classification",
                system_prompt, user_prompt,
                error=str(e),
            )
            raise

    def refer_folder_task_sequence(
        self,
        node: FolderNode,
        file_index: Dict[str, FileIndexEntry],
        folder_stats: FolderStats,
        concat_desc: str,
        category: Category,
        folder_description: str = "",
        ancestor_descriptions: Optional[List[str]] = None,
        known_task_names: Optional[Set[str]] = None,
    ) -> TaskSequenceResult:
        """
        Infer task_name / sequence_name for a folder in a separate LLM call.
        """
        system_prompt = self._folder_task_system_prompt()
        user_prompt = self._folder_task_user_prompt(
            node,
            file_index,
            folder_stats,
            concat_desc,
            category,
            folder_description,
            ancestor_descriptions,
            known_task_names,
        )

        try:
            resp = self.client.responses.parse(
                model=self.model,
                instructions=system_prompt,
                input=[{"role": "user", "content": user_prompt}],
                text_format=LLMFolderTaskSequenceDecision,
            )
            decision: LLMFolderTaskSequenceDecision = resp.output_parsed

            self._log_call(
                "folder_task_sequence_reference",
                system_prompt, user_prompt,
                raw=str(resp),
                parsed=decision.model_dump(),
            )

            task_name = _sanitize_task_name(decision.task_name)

            return TaskSequenceResult(
                reason=decision.reason,
                task_name=task_name,
                sequence_name=None,
                category_depth=_category_depth(
                    node.path,
                    task_name,
                    None,
                    decision.category_depth,
                ),
                by_type=decision.by_type,
            )

        except Exception as e:
            self._log_call(
                "folder_task_sequence_reference",
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
        """Classify an individual file. Task/sequence metadata is separate."""
        db_entry = file_index.get(file_meta.file_name)
        file_desc = ""
        if db_entry and db_entry.description:
            file_desc = db_entry.description
        elif file_meta.description:
            file_desc = file_meta.description

        if _is_youtube_course_video(file_meta, file_desc):
            reason = "YouTube/course-video material is treated as study content because it contains course video."
            self._log_call(
                "file_classification",
                "deterministic youtube course-video rule",
                f"File: {file_meta.source_path}\nDescription: {file_desc or '[none available]'}",
                parsed={
                    "file_path": file_meta.source_path,
                    "reason": reason,
                    "category": Category.STUDY.value,
                },
            )
            return ClassificationResult(
                category=Category.STUDY,
                reason=reason,
            )

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

        except APITimeoutError as e:
            self._log_call(
                "file_classification",
                system_prompt, user_prompt,
                error=f"timeout after {self.timeout_seconds}s: {e}",
            )
            raise
        except Exception as e:
            self._log_call(
                "file_classification",
                system_prompt, user_prompt,
                error=str(e),
            )
            raise

    def refer_file_task_sequence(
        self,
        file_meta: FileMeta,
        file_index: Dict[str, FileIndexEntry],
        category: Category,
        ancestor_descriptions: Optional[List[str]] = None,
        sibling_names: Optional[List[str]] = None,
        known_task_names: Optional[Set[str]] = None,
    ) -> TaskSequenceResult:
        """Infer task_name / sequence_name for an individual file."""
        db_entry = file_index.get(file_meta.file_name)
        file_desc = ""
        if db_entry and db_entry.description:
            file_desc = db_entry.description
        elif file_meta.description:
            file_desc = file_meta.description

        system_prompt = self._file_task_system_prompt()
        user_prompt = self._file_task_user_prompt(
            file_meta,
            file_desc,
            category,
            ancestor_descriptions,
            sibling_names,
            known_task_names,
        )

        try:
            resp = self.client.responses.parse(
                model=self.model,
                instructions=system_prompt,
                input=[{"role": "user", "content": user_prompt}],
                text_format=LLMFileTaskSequenceDecision,
            )
            decision: LLMFileTaskSequenceDecision = resp.output_parsed

            self._log_call(
                "file_task_sequence_reference",
                system_prompt, user_prompt,
                raw=str(resp),
                parsed=decision.model_dump(),
            )

            task_name = _sanitize_task_name(decision.task_name)

            return TaskSequenceResult(
                reason=decision.reason,
                task_name=task_name,
                sequence_name=None,
                category_depth=_category_depth(
                    file_meta.source_path,
                    task_name,
                    None,
                    decision.category_depth,
                ),
            )

        except APITimeoutError as e:
            self._log_call(
                "file_task_sequence_reference",
                system_prompt, user_prompt,
                error=f"timeout after {self.timeout_seconds}s: {e}",
            )
            raise
        except Exception as e:
            self._log_call(
                "file_task_sequence_reference",
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
            task_result = self.refer_file_task_sequence(
                file_meta, file_index,
                category=result.category,
                ancestor_descriptions=ancestor_descriptions,
                sibling_names=siblings if siblings else None,
            )
            result.task_name = task_result.task_name
            result.sequence_name = task_result.sequence_name
            results[file_meta.source_path] = result

        logger.info(f"[classify_files] Completed: {total} files classified")
        return results

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
            "then set is_mixed."
        )

        return "\n".join(lines)

    def _folder_task_system_prompt(self) -> str:
        """System prompt for folder task extraction."""
        return (
            "You are analyzing a course folder and must extract task metadata only.\n\n"
            "Do NOT classify the category or organization flags; they are already provided by another call.\n\n"
            "You must determine:\n"
            "1) task_name     (string or \"\")\n"
            "2) seq_name      (always \"\"; sequence is inferred later from batches of files)\n"
            "3) category_depth (integer from 0 to max depth of this folder path)\n"
            "4) by_type       (boolean)\n\n"
            "--- TASK EXTRACTION ---\n"
            "task_name:\n"
            "- Infer a concise lowercase label from the actual folder path, names, descriptions, and context.\n"
            "- Prefer labels already present in Known task names when they fit semantically.\n"
            "- Use a new label when the evidence clearly supports it; otherwise use \"\".\n"
            "- Keep the label stable and semantic enough to group similar course materials.\n\n"
            "- Prefer the most precise matching material/task word supported by the path or filename.\n"
            "- For lecture-area materials, avoid broad task_name \"lecture\" when a more specific label fits.\n"
            "- Use task_name \"slides\" for slide decks, lecture PDFs, presentation files, or lecture handouts "
            "whose purpose is slide/reading review.\n"
            "- Use task_name \"boardwork\" for boardwork / whiteboard writing / handwritten board notes.\n"
            "- Treat folder names like lec/lecture/lectures as context, not automatically as task_name \"lecture\".\n\n"
            "- Do not use the broad category itself as task_name. Do not return "
            "\"study\", \"practice\", or \"support\" as task_name.\n\n"
            "seq_name:\n"
            "- Always return \"\". Do not infer sequence names in this call.\n\n"
            "category_depth:\n"
            "- Use the deepest folder level in the input path where task_name appears.\n"
            "- Count levels from the category root after reorganization: category is level 0, the first folder under it is level 1, then level 2, and so on.\n"
            "- If task_name is not identified in this folder path, use 0.\n"
            "- category_depth may be greater than 2 for deeper paths.\n\n"
            "by_type:\n"
            "- Set true if this folder's immediate children are organised by TASK/MEDIA TYPE, "
            "so each child folder name should be treated as its own task_name.\n"
            "- Example: practice/ contains hw/, lab/, proj/ as different task types.\n"
            "- Set false when children are sequential/topic variants of the same task or when unclear.\n\n"
            "Return a JSON object with EXACT keys:\n"
            "{\n"
            '  "folder_path": "<exact input path>",\n'
            '  "reason": "...",\n'
            '  "task_name": "...",\n'
            '  "seq_name": "...",\n'
            '  "category_depth": <integer>,\n'
            '  "by_type": <boolean>\n'
            "}\n"
        )

    def _folder_task_user_prompt(
        self,
        node: FolderNode,
        file_index: Dict[str, FileIndexEntry],
        stats: FolderStats,
        concat_desc: str,
        category: Category,
        folder_description: str = "",
        ancestor_descriptions: Optional[List[str]] = None,
        known_task_names: Optional[Set[str]] = None,
    ) -> str:
        """Build the user prompt for folder task extraction."""
        lines: List[str] = []

        if ancestor_descriptions:
            lines.append("Ancestor context (root -> parent):")
            for i, desc in enumerate(ancestor_descriptions):
                lines.append(f"  [{i}] {desc}")
            lines.append("")

        if known_task_names:
            known_str = ", ".join(f'"{n}"' for n in sorted(known_task_names))
            lines.append(f"Known task names (prefer these if suitable): {known_str}")
            lines.append("")

        lines.append(f"Folder: {node.path}")
        lines.append(f"Name: {node.name}")
        lines.append(f"Already classified category: {category.value}")
        if folder_description:
            lines.append(f"Folder description: {folder_description}")
        lines.append(f"TotalFiles: {stats.total_file_count}")
        lines.append(f"ImmediateFiles: {stats.immediate_file_count}")
        lines.append(f"SubfolderCount: {stats.subfolder_count}")

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

            lines.append("\nDB metadata for files:")
            for f in all_files:
                entry = file_index.get(f.file_name)
                if not entry:
                    lines.append(f"  - {f.file_name} :: [no DB entry found]")
                    continue

                db_fields = {
                    "uuid": entry.uuid,
                    "file_name": entry.file_name,
                    "description": entry.description,
                    "original_path": entry.original_path,
                    "relative_path": entry.relative_path,
                    "extra_info": entry.extra_info,
                    "file_hash": entry.file_hash,
                }
                compact = json.dumps(db_fields, ensure_ascii=False)
                lines.append(f"  - {f.file_name} :: {compact}")

        if concat_desc:
            lines.append("\nConcatenated file descriptions:")
            lines.append(concat_desc)

        lines.append("\nInfer task metadata only. Write reason FIRST, then fill the fields. Leave seq_name blank.")
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

    def _file_task_system_prompt(self) -> str:
        """System prompt for file task extraction."""
        return (
        "You are analyzing a single course file and must extract task metadata only.\n\n"
        "Do NOT classify the category; it is already provided.\n\n"
        "You must determine:\n"
        "1) task_name  (short normalized label inferred from context)\n"
        "2) seq_name   (always blank; sequence is inferred later from batches of files)\n"
        "3) category_depth (integer from 0 to max depth of this file path)\n\n"
        "--- TASK EXTRACTION ---\n"
        "task_name:\n"
        "- Infer a concise lowercase label from the actual file path, name, description, and nearby context.\n"
        "- Prefer labels already present in Known task names when they fit semantically.\n"
        "- Use a new label when the evidence clearly supports it; otherwise use \"\".\n"
        "- Keep the label stable and semantic enough to group similar course materials.\n\n"
        "- Prefer the most precise matching material/task word supported by the path or filename.\n"
        "- For lecture-area materials, avoid broad task_name \"lecture\" when a more specific label fits.\n"
        "- Use task_name \"slides\" for slide decks, lecture PDFs, presentation files, or lecture handouts "
        "whose purpose is slide/reading review.\n"
        "- Use task_name \"boardwork\" for boardwork / whiteboard writing / handwritten board notes.\n"
        "- Treat folder names like lec/lecture/lectures as context, not automatically as task_name \"lecture\".\n\n"
        "- Do not use the broad category itself as task_name. Do not return "
        "\"study\", \"practice\", or \"support\" as task_name.\n\n"
        "seq_name:\n"
        "- Always return \"\". Do not infer sequence names in this call.\n\n"
        "category_depth:\n"
        "- Use the deepest folder level in the input path where task_name appears.\n"
        "- If task_name is identified from the filename itself, use the file's containing-folder depth.\n"
        "- Count levels from the category root after reorganization: category is level 0, the first folder under it is level 1, then level 2, and so on.\n"
        "- If task_name is not identified in this file path, use 0.\n"
        "- category_depth may be greater than 2 for deeper paths.\n\n"
        "Return a JSON object with EXACT keys:\n"
        "{\n"
        '  "file_path": "<exact input path>",\n'
        '  "reason": "...",\n'
        '  "task_name": "...",\n'
        '  "seq_name": "...",\n'
        '  "category_depth": <integer>\n'
        "}\n"
        )

    def _file_task_user_prompt(
        self,
        file_meta: FileMeta,
        file_desc: str,
        category: Category,
        ancestor_descriptions: Optional[List[str]] = None,
        sibling_names: Optional[List[str]] = None,
        known_task_names: Optional[Set[str]] = None,
    ) -> str:
        """Build user prompt for file task extraction."""
        lines: List[str] = []

        if ancestor_descriptions:
            lines.append("Ancestor context (root -> parent):")
            for i, desc in enumerate(ancestor_descriptions):
                lines.append(f"  [{i}] {desc}")
            lines.append("")

        if known_task_names:
            known_str = ", ".join(f'"{n}"' for n in sorted(known_task_names))
            lines.append(f"Known task names (prefer these if suitable): {known_str}")
            lines.append("")

        lines.append(f"File: {file_meta.source_path}")
        lines.append(f"Name: {file_meta.file_name}")
        lines.append(f"Parent folder: {file_meta.folder_path}")
        lines.append(f"Already classified category: {category.value}")

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

        lines.append("\nInfer task metadata only. Write reason FIRST, then fill task_name. Leave seq_name blank.")
        return "\n".join(lines)

    # -------------------- Sequence Name Inference -------------------- #

    def _sequence_system_prompt(self) -> str:
        """System prompt for grouped sequence extraction."""
        return (
            "You are helping organize course files and folders by assigning sequence names within a batch.\n"
            "Every input item already has category and task_name. Do NOT change task_name or category.\n\n"
            "Goal:\n"
            "- Identify seq_name by comparing the batch together, so you can see lecture, homework, "
            "discussion, part, or serial-number patterns across sibling files.\n"
            "- Use the task-group context to compare against other files and folders that share the same task_name.\n"
            "- Use \"\" when no stable sequence marker is supported.\n\n"
            "Rules:\n"
            "1. Prefer sequence labels directly supported by filenames or path components.\n"
            "2. Preserve the numeric parts as written; for example, use 2_23 for 2_23.pdf and 118 for 118_disc.pdf.\n"
            "3. Preserve meaningful typed sequence labels when present, e.g. Homework_5, Discussion_10, lecture03.\n"
            "4. When files in the same task batch share a textual sequence pattern, use that same textual pattern "
            "with each file's own number marker. For example, prefer Homework_4, Homework_5, Homework_6 over "
            "4, 5, 6; prefer Discussion_1, Discussion_2 over 1, 2; prefer lecture01, lecture02 over 01, 02.\n"
            "5. Treat minor naming differences as the same sequence when they share the same task and number marker. "
            "For example, hw3.zip and Homework_3__Path_Planning.pdf should both use Homework_3.\n"
            "6. Prefer the most descriptive consistent label already visible in the batch or task-group context.\n"
            "7. If the shared text is merely the task_name itself and adds no sequence meaning, do not duplicate it; "
            "use the file's own number marker instead.\n"
            "8. Leave seq_name blank when the label is not a clear sequence/order marker. "
            "Do not invent alphanumeric fragments from topic words, e.g. feedback_linearization should not become and3D.\n"
            "9. seq_name must be the ordering/part marker, not the task type itself.\n"
            "10. category_depth is the deepest source path folder level where sequence metadata appears. "
            "If the sequence is identified from the filename, use the file's containing-folder depth.\n"
            "11. For folder items, set by_sequence=true if the folder's direct children are organised by "
            "TOPIC/TIME SEQUENCE, such as lecture01/, lecture02/ or project topic folders in order. "
            "Set false for file items and for type-grouped or unclear folders.\n"
            "12. Return one item for every input source_path, using the exact source_path. "
            "The source_path may refer to a file or folder.\n\n"
            "Return a JSON object with EXACT key \"items\", where each item has:\n"
            "{\n"
            '  "source_path": "<exact input path>",\n'
            '  "reason": "...",\n'
            '  "seq_name": "...",\n'
            '  "by_sequence": <boolean>,\n'
            '  "category_depth": <integer>\n'
            "}\n"
        )

    def _sequence_user_prompt(
        self,
        items: List[Dict[str, str]],
        task_context: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        """Build the user prompt for grouped sequence extraction."""
        lines: List[str] = []
        first = items[0]
        lines.append(f"Batch category: {first.get('category', '')}")
        lines.append(f"Batch task_name: {first.get('task_name', '')}")
        if task_context:
            lines.append("\nTask-group context (files and folders with the same category/task_name):")
            for item in task_context:
                item_type = item.get("type", "")
                lines.append(f"- type: {item_type}")
                lines.append(f"  path: {item.get('path', '')}")
                if item.get("name"):
                    lines.append(f"  name: {item.get('name', '')}")
                if item.get("parent_folder"):
                    lines.append(f"  parent_folder: {item.get('parent_folder', '')}")
                if item.get("sequence_name"):
                    lines.append(f"  existing_sequence_name: {item.get('sequence_name', '')}")
                if item.get("category_depth"):
                    lines.append(f"  category_depth: {item.get('category_depth', '')}")
                desc = (item.get("description") or "").replace("\n", " ").strip()
                if desc:
                    lines.append(f"  description: {desc}")

        lines.append("\nItems to infer:")
        for item in items:
            lines.append(f"- source_path: {item.get('source_path', '')}")
            if item.get("item_type"):
                lines.append(f"  item_type: {item.get('item_type', '')}")
            lines.append(f"  name: {item.get('file_name', '')}")
            lines.append(f"  parent_folder: {item.get('parent_folder', '')}")
            desc = (item.get("description") or "").replace("\n", " ").strip()
            if desc:
                lines.append(f"  description: {desc}")

        return "\n".join(lines)

    def infer_sequence_names_batch(
        self,
        items: List[Dict[str, str]],
        task_context: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, LLMSequenceBatchItem]:
        """
        Infer sequence names for a batch of files/folders that already share a task name.
        Returns a mapping from source_path to the parsed sequence decision.
        """
        if not items:
            return {}

        system_prompt = self._sequence_system_prompt()
        user_prompt = self._sequence_user_prompt(items, task_context)

        try:
            resp = self.client.responses.parse(
                model=self.model,
                instructions=system_prompt,
                input=[{"role": "user", "content": user_prompt}],
                text_format=LLMSequenceBatchDecision,
            )
            decision: LLMSequenceBatchDecision = resp.output_parsed

            self._log_call(
                "sequence_name_batch_inference",
                system_prompt, user_prompt,
                raw=str(resp),
                parsed=decision.model_dump(),
            )

            return {item.source_path: item for item in decision.items}

        except Exception as e:
            self._log_call(
                "sequence_name_batch_inference",
                system_prompt, user_prompt,
                error=str(e),
            )
            raise

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
            "A task name identifies the type of educational task or content group the file belongs to.\n\n"
            "You will receive:\n"
            "  - The file/folder path\n"
            "  - Its category (study / practice / support)\n"
            "  - Its description (if available)\n"
            "  - Ancestor folder context\n"
            "  - A set of known task names already identified in this course\n\n"
            "Rules:\n"
            "1. Prefer matching a name from the known_task_names set when it fits.\n"
            "2. If none fit, propose a concise lowercase task name supported by the path, description, or context.\n"
            "3. Reason FIRST, then fill task_name.\n"
            "4. source_path MUST match exactly the path shown in the input.\n"
            "5. Use \"\" if no meaningful label is supported.\n"
            "6. Prefer the most precise matching material/task word. Exact words in the filename or path "
            "are stronger evidence than broad course-context folder names.\n"
            "7. For lecture-area materials, avoid broad task_name \"lecture\" when a more specific label fits; "
            "prefer \"slides\" for slide decks/lecture PDFs/presentation files and \"boardwork\" for "
            "whiteboard or handwritten board notes.\n"
            "8. Do not use the category itself as task_name; avoid study/practice/support as task labels.\n"
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
            return _sanitize_task_name(decision.task_name)

        except Exception as e:
            self._log_call(
                "task_name_inference",
                system_prompt, user_prompt,
                error=str(e),
            )
            logger.warning(f"[LLM] infer_task_name failed for {source_path}: {e}")
            return None

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
