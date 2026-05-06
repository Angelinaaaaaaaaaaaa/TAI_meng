"""Smoke tests — verify the package imports and basic helpers work.

Real coverage is the highest-priority follow-up; this file exists so the
``tests/`` directory isn't empty and ``pytest`` has something to discover.
"""

import sys
from pathlib import Path

# Make the package importable when running pytest from the project root.
_PARENT = Path(__file__).resolve().parent.parent.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

from rearrange.src.core.steps import (
    build_tree_from_final_paths,
    reorganize_tree_by_final_paths,
)
from rearrange.src.services.llm_gateway import (
    DEFAULT_LLM_MODEL,
    DEFAULT_LLM_SEED,
)
from rearrange.src.utils.utils import (
    _derive_course_name,
    _is_under_path,
    _normalize_path,
)


def test_path_helpers():
    assert _normalize_path("/foo/bar/") == "/foo/bar"
    assert _is_under_path("a/b/c", "a/b") is True
    assert _is_under_path("a/bb", "a/b") is False  # slash boundary


def test_course_name_derivation():
    assert _derive_course_name("CS 61A_metadata.db") == "CS_61A"
    assert _derive_course_name(None, "bfs_v3_tree_cs61a.json") == "cs61a"


def test_constants_pinned():
    assert DEFAULT_LLM_MODEL == "gpt-5-mini-2025-08-07"
    assert DEFAULT_LLM_SEED == 42


def test_build_from_empty_doc_yields_empty_tree():
    tree = build_tree_from_final_paths({"all_final_paths": []})
    assert tree["name"] == "root"
    assert tree["children"] == {}
    assert tree["files"] == {}


def test_reorganize_with_single_entry():
    raw_tree = {
        "name": "root",
        "type": "folder",
        "children": {},
        "files": {
            "abc": {
                "type": "file",
                "name": "x.pdf",
                "path": "raw/x.pdf",
                "file_hash": "abc",
            }
        },
    }
    doc = {
        "all_final_paths": [
            {
                "source": "raw/x.pdf",
                "final_path": "study/topic/x.pdf",
                "category": "study",
                "task_name": "topic",
                "sequence_name": None,
            }
        ]
    }
    out = reorganize_tree_by_final_paths(raw_tree, doc)
    study = out["children"]["study"]
    assert study["category"] == "study"
    files = study["children"]["topic"]["files"]
    assert any(f.get("file_hash") == "abc" for f in files.values())
