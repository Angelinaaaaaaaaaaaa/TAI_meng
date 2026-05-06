"""Characterization and unit tests for ``file_rearrang`` pipeline helpers."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REARRANGE_DIR = Path(__file__).resolve().parents[2]
assert REARRANGE_DIR.joinpath("file_rearrang.py").is_file(), f"Missing rearrange dir: {REARRANGE_DIR}"
sys.path.insert(0, str(REARRANGE_DIR))

import file_rearrang as fr  # noqa: E402


def test_normalize_path_and_under_path() -> None:
    assert fr._normalize_path("  a/b/  ") == "a/b"
    assert fr._is_under_path("study/slides/a", "study/slides") is True
    assert fr._is_under_path("study/other", "study/slides") is False


def test_derive_course_name() -> None:
    assert "EECS" in fr._derive_course_name("EECS 106B_metadata.db", None)
    assert fr._derive_course_name(None, "bfs_v3_tree_cs61a_new.json") == "cs61a_new"


def test_build_summary_truncation() -> None:
    rows = [{"description": "x" * 100}]
    out = fr.build_summary(rows, truncate_fields={"description": 10})
    assert out[0]["description"] == "x" * 10 + "..."


def test_enrich_structure_minimal(tmp_path: Path) -> None:
    inp = tmp_path / "in.json"
    dbp = tmp_path / "meta.db"
    outp = tmp_path / "out.json"

    tree = {
        "name": "course",
        "type": "folder",
        "children": {
            "study": {
                "name": "study",
                "type": "folder",
                "category": "study",
                "children": {
                    "f.txt": {"name": "f.txt", "type": "file"},
                },
            }
        },
    }
    inp.write_text(json.dumps(tree), encoding="utf-8")

    conn = sqlite3.connect(dbp)
    conn.execute(
        "CREATE TABLE file (relative_path TEXT, file_name TEXT, description TEXT)"
    )
    conn.execute(
        "INSERT INTO file VALUES ('course/study/f.txt', 'f.txt', 'Test description')"
    )
    conn.commit()
    conn.close()

    result = fr.enrich_structure_with_descriptions(str(inp), str(dbp), str(outp))
    assert result == str(outp)
    data = json.loads(outp.read_text(encoding="utf-8"))
    assert data["name"] == "study"
    assert data["relative_path"] == "study"


def test_collect_orphan_items_skips_backbone() -> None:
    enriched = {
        "name": "study",
        "children": [
            {
                "name": "backbone",
                "type": "folder",
                "relative_path": "study/backbone",
                "children": [
                    {
                        "name": "a.pdf",
                        "type": "file",
                        "relative_path": "study/backbone/a.pdf",
                        "description": "in backbone",
                    }
                ],
            },
            {
                "name": "extra",
                "type": "folder",
                "relative_path": "study/extra",
                "children": [
                    {
                        "name": "b.pdf",
                        "type": "file",
                        "relative_path": "study/extra/b.pdf",
                        "description": "orphan file",
                    }
                ],
            },
        ],
    }
    orphans = fr.collect_orphan_items(enriched, "study/backbone")
    paths = {o["structure_path"] for o in orphans}
    assert "study/backbone/a.pdf" not in paths
    assert any("extra" in p for p in paths)


def test_generate_rearrangement_plan_no_misc_llm(tmp_path: Path) -> None:
    groups = [
        fr.BackboneGroup(
            group_name="Unit1",
            main_item="study/u1",
            related_items=[],
            description="u1",
        ),
        fr.BackboneGroup(
            group_name="Lecture Miscellaneous",
            main_item="",
            related_items=[],
            description="misc",
        ),
    ]
    matches = fr.OrphanMatchResponse(matches=[])
    token = fr.set_pipeline_log_dir(str(tmp_path / "logs"))
    try:
        plan = fr.generate_rearrangement_plan(groups, matches, llm_gateway=None)
    finally:
        fr.reset_pipeline_log_dir(token)
    names = {p["group_name"] for p in plan}
    assert "Unit1" in names
    assert "Lecture Miscellaneous" in names


def test_append_unmatched_orphans_to_misc() -> None:
    orphans = [
        {
            "relative_path": "study/extra/a.pdf",
            "structure_path": "study/extra/a.pdf",
        },
        {
            "relative_path": "study/extra/b.pdf",
            "structure_path": "study/extra/b.pdf",
        },
    ]
    matches = [
        fr.OrphanMatch(item_path="study/extra/a.pdf", assigned_group="Unit1"),
    ]

    appended = fr._append_unmatched_orphans_to_misc(orphans, matches)

    assert appended == 1
    assert any(
        m.item_path == "study/extra/b.pdf"
        and m.assigned_group == "Lecture Miscellaneous"
        for m in matches
    )


def test_llm_gateway_parse_structured() -> None:
    fake_client = MagicMock()
    parsed = fr.BackboneResult(backbone_path="study/slides")
    msg = MagicMock()
    msg.parsed = parsed
    choice = MagicMock()
    choice.message = msg
    completion = MagicMock()
    completion.choices = [choice]
    fake_client.beta.chat.completions.parse.return_value = completion

    gw = fr.LLMGateway(client=fake_client)
    out = gw.parse_structured(
        model="gpt-5-mini",
        system_prompt="sys",
        user_payload={"a": 1},
        response_model=fr.BackboneResult,
    )
    assert out.backbone_path == "study/slides"
    fake_client.beta.chat.completions.parse.assert_called_once()
