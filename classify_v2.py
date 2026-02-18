#!/usr/bin/env python3
"""
classify_v1.py

LLM-based Classifier for course folders and files.

Extracted from bfs_v2.py so the classifier is a standalone, importable module.
Uses prompts adapted from file_organizer_v6.py.

Features:
  - Single LLM call per folder: reason + decide + summarize (FIX #3)
  - Structured output via Pydantic models
  - Full debug logging of every LLM call (system prompt, user prompt, response)
  - Ancestor descriptions passed as context for hierarchical classification

Usage:
    from classify_v2 import LLMClassifier

    classifier = LLMClassifier(model="gpt-4.1-2025-04-14")
    result = classifier.classify_folder(node, file_index, stats, concat_desc)
    result = classifier.classify_file(file_meta, file_index)
    (New) results = classifier.classify_files(file_list, file_index)
    classifier.save_debug_log("debug.json")
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


# ====================================================================
#  Constants
# ====================================================================

MAX_CONCAT_DESC_CHARS = 6000
MAX_FILES_IN_PROMPT = 50
LLM_DEBUG_LOG_FILE = "bfs_v2_llm_debug.json"


class Category(str, Enum):
    STUDY = "study"
    PRACTICE = "practice"
    SUPPORT = "support"
    SKIP = "skip"


# ====================================================================
#  Shared Data Structures (imported by bfs_v2)
# ====================================================================

# These are defined here so both classify_v2 and bfs_v2 use the same types
# without circular imports. bfs_v2 re-exports them.

from dataclasses import dataclass, field
from collections import deque


@dataclass
class FileMeta:
    """Metadata for a single file."""
    source_path: str
    folder_path: str
    file_name: str
    description: Optional[str] = None


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


DEFAULT_CONFIDENCE_THRESHOLD = 0.75


@dataclass
class ClassificationResult:
    """Result of classifying a folder or file."""
    category: Category
    confidence: float
    reason: str
    is_mixed: bool = False
    folder_description: str = ""

    def is_confident(self, threshold: float = DEFAULT_CONFIDENCE_THRESHOLD) -> bool:
        return self.confidence >= threshold and not self.is_mixed


# ====================================================================
#  Pydantic Models for LLM Structured Output
# ====================================================================

class LLMFolderDecision(BaseModel):
    """LLM output for a single folder: reason FIRST, then category."""
    folder_path: str = Field(..., description="Exact folder path from input")
    reason: str = Field(
        ..., min_length=10,
        description=(
            "MUST be filled FIRST. Detailed reasoning about what this folder "
            "contains, its purpose, and why it belongs in the chosen category. "
            "Think step-by-step BEFORE choosing a category."
        ),
    )
    category: str = Field(
        ..., pattern="^(practice|study|support|skip)$",
        description="Category decided AFTER reasoning.",
    )
    confidence: float = Field(
        ..., ge=0.0, le=1.0,
        description="Confidence score (0-1) decided AFTER reasoning.",
    )
    is_mixed: bool = Field(
        False,
        description="True if the folder contains semantically mixed content.",
    )
    folder_description: str = Field(
        "",
        description="One-sentence summary of the folder's pedagogical purpose.",
    )


class LLMFileDecision(BaseModel):
    """LLM output for a single file classification."""
    file_path: str = Field(..., description="Exact file path from input")
    reason: str = Field(
        ..., min_length=5,
        description="Reasoning about this file's educational purpose.",
    )
    category: str = Field(
        ..., pattern="^(practice|study|support|skip)$",
    )
    confidence: float = Field(..., ge=0.0, le=1.0)


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

    Uses prompts adapted from file_organizer_v6.py and file_classifier.py.
    Consolidates reasoning + decision into a single LLM call per item.
    """

    def __init__(self, model: str = "gpt-4.1-2025-04-14"):
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

        Args:
            node: FolderNode to classify
            file_index: Full DB file index (available for subclass overrides)
            folder_stats: Pre-computed structural statistics
            concat_desc: Concatenated file descriptions
            ancestor_descriptions: Context from parent folders

        Returns:
            ClassificationResult with category, confidence, reason, is_mixed,
            and folder_description
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

            return ClassificationResult(
                category=Category(decision.category),
                confidence=decision.confidence,
                reason=decision.reason,
                is_mixed=decision.is_mixed,
                folder_description=decision.folder_description,
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
        """
        Classify an individual file.

        Args:
            file_meta: The file to classify
            file_index: Full DB file index (for description lookup)
            ancestor_descriptions: Context from ancestor folders
            sibling_names: Names of other files in the same directory (for
                context about naming conventions and co-located files)
        """
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

            return ClassificationResult(
                category=Category(decision.category),
                confidence=decision.confidence,
                reason=decision.reason,
                is_mixed=False,
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
        """
        Classify a list of files one-by-one, returning a mapping of
        file_path -> ClassificationResult.

        Each file is classified with its own LLM call for detailed reasoning.
        Sibling file names from the same directory are automatically gathered
        and passed as context to help the LLM apply naming-convention heuristics.

        Args:
            files: List of FileMeta objects to classify
            file_index: Full DB file index (for description lookup)
            ancestor_descriptions: Context from ancestor folders

        Returns:
            Dict mapping source_path -> ClassificationResult
        """
        results: Dict[str, ClassificationResult] = {}

        if not files:
            return results

        # Pre-compute sibling names per directory for naming-convention context
        dir_to_names: Dict[str, List[str]] = {}
        for f in files:
            dir_to_names.setdefault(f.folder_path, []).append(f.file_name)

        total = len(files)
        for i, file_meta in enumerate(files, 1):
            logger.info(
                f"[classify_files] ({i}/{total}) Classifying: {file_meta.source_path}"
            )

            # Gather sibling names (exclude the file itself)
            siblings = [
                name for name in dir_to_names.get(file_meta.folder_path, [])
                if name != file_meta.file_name
            ]

            result = self.classify_file(
                file_meta,
                file_index,
                ancestor_descriptions=ancestor_descriptions,
                sibling_names=siblings if siblings else None,
            )
            results[file_meta.source_path] = result

        logger.info(
            f"[classify_files] Completed: {total} files classified"
        )
        return results

    # -------------------- Prompts (adapted from file_organizer_v6) -------------------- #

    def _folder_system_prompt(self) -> str:
        """System prompt for folder classification."""
        return (
            "You are classifying course folders into four categories:\n"
            "- practice: Students DO or PRODUCE work — homework (hw), labs, projects.\n"
            "  This also includes exam solutions/explanations (e.g., midterm walkthroughs).\n"
            "- study: Instructional learning content that students READ, WATCH, or REVIEW.\n"
            "  This includes: lectures, lecture slides/notes/PDFs, readings, videos,\n"
            "  discussion/section materials and folders containing lecture slides/PDFs/code.\n"
            "- support: Global course support like syllabus, past exams, textbooks,\n"
            "  tools/how-to docs.\n"
            "- skip: Build artifacts, generated files, empty folders, or content with\n"
            "  no pedagogical value that should not be reorganized.\n\n"
            "Key distinction:\n"
            "  'practice' = student-produced assignments (hw, lab, project).\n"
            "  'study'    = instructor-provided learning material.\n"
            "  Discussion worksheets are study material even when they contain problems,\n"
            "  because they are part of the instructor-led section flow, not graded\n"
            "  student submissions.\n\n"
            "Rules:\n"
            "1. You receive a description of a folder with its structure, file listings,\n"
            "   and concatenated file descriptions from the database.\n"
            "2. You may also receive ancestor descriptions providing hierarchical context.\n"
            "3. Your primary goal is to assign ONE best category based on overall purpose.\n"
            "4. Think top-down: classify the folder as a whole, not per-file.\n"
            "5. CRITICAL — you MUST reason FIRST, then decide:\n"
            "   a. Write a detailed 'reason' field explaining what the folder contains,\n"
            "      its educational purpose, and why it fits a particular category.\n"
            "   b. Only AFTER writing the reason, fill in 'category' and 'confidence'.\n"
            "   c. The reason must logically support the chosen category.\n"
            "   DO NOT pick a category first and then justify it.\n"
            "6. Set is_mixed=true if the folder contains a clear mix of categories\n"
            "   (e.g., both homework and lecture slides). This signals that the BFS\n"
            "   should descend and classify children individually.\n"
            "7. Write a brief folder_description (one sentence) summarizing the\n"
            "   folder's pedagogical purpose. This will be used as context for\n"
            "   classifying child folders/files.\n"
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
            cap = min(len(all_files), MAX_FILES_IN_PROMPT)
            lines.append(f"\nFiles (name + description, up to {cap} shown):")
            for f in all_files[:cap]:
                desc = (f.description or "").replace("\n", " ").strip()
                if not desc:
                    desc = "[no description]"
                lines.append(f"  - {f.file_name} :: {desc}")
            if len(all_files) > cap:
                lines.append(f"  ... and {len(all_files) - cap} more files")

        if concat_desc:
            lines.append(f"\nConcatenated file descriptions (up to {MAX_CONCAT_DESC_CHARS} chars):")
            lines.append(concat_desc)

        lines.append(
            "\nClassify this folder. Write reason FIRST, then category and confidence."
        )

        return "\n".join(lines)

    def _file_system_prompt(self) -> str:
        """System prompt for individual file classification."""
        return (
            "You are classifying a single course file into one of four categories:\n\n"
            "- study: Learning materials that students READ, WATCH, or REVIEW.\n"
            "  Includes: lecture slides, lecture notes/PDFs, readings, videos,\n"
            "  discussion/section materials, supplement code files for demonstration\n"
            "  (not for practicing). These materials usually focus on specific\n"
            "  course concepts.\n\n"
            "- practice: Student-produced work and assignments.\n"
            "  Includes: homework, labs, projects, exercises, lab sheets,\n"
            "  quizzes, exam papers, Jupyter notebooks for assignments (.ipynb).\n\n"
            "- support: Course logistics and supplementary resources.\n"
            "  Includes: syllabus, calendar, tools/how-to docs, study guides,\n"
            "  extracurricular readings, cheat sheets, past exams (when used\n"
            "  as reference, not as active assignments).\n\n"
            "- skip: Generated/irrelevant files with no pedagogical value.\n"
            "  Includes: build artifacts, cache files, empty files, compiled\n"
            "  binaries, package lock files.\n\n"
            "Key distinctions:\n"
            "  'practice' = student-produced assignments (hw, lab, project).\n"
            "  'study'    = instructor-provided learning material.\n"
            "  Discussion worksheets are study material even when they contain\n"
            "  problems, because they are part of instructor-led section flow.\n\n"
            "Rules:\n"
            "1. You receive the file's name, path, description from the database,\n"
            "   and context from ancestor folders.\n"
            "2. You may also receive a list of sibling files in the same directory.\n"
            "   Files in the same folder with similar naming conventions usually\n"
            "   belong to the same category — use this as a strong signal.\n"
            "3. Reason FIRST about what this file is and its educational purpose.\n"
            "4. Then decide on category and confidence.\n"
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
            "\nClassify this file. Write reason FIRST, then category and confidence."
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
