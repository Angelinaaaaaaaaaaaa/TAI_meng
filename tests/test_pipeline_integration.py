"""
Integration tests for the end-to-end orchestration pipeline.

Architecture:
  Part 1 (BFS):      bfs_v4.bfs_reorganize()  →  bfs_v4_final_paths.json + bfs_v4_tree.json
  Part 2 (Rearrange): rearrange pipeline       →  rearrangement_structure_tree.json
  Part 3 (Eval):     evaluation (optional)     →  evaluation_report.json

These tests follow strict TDD: they were written BEFORE the implementation in
pipeline_orchestrator.py exists, so they must FAIL on the first run.
"""

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

# Make project importable
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pipeline_orchestrator import (
    BfsStageResult,
    OrchestratorConfig,
    PipelineResult,
    RunMode,
    orchestrate,
    run_bfs_stage,
    run_evaluation_stage,
    run_rearrange_stage,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_project(tmp_path: Path) -> Path:
    """Create a minimal project layout under a temp directory."""
    (tmp_path / "input").mkdir()
    (tmp_path / "outputs").mkdir()
    return tmp_path


@pytest.fixture()
def minimal_final_paths_doc() -> Dict[str, Any]:
    return {
        "metadata": {"total_files": 2, "known_task_names": ["slides"]},
        "all_final_paths": [
            {
                "source": "lec/lec01.pdf",
                "category": "study",
                "task_name": "slides",
                "sequence_name": "1",
                "category_depth": 1,
                "final_path": "study/slides/1/lec01.pdf",
            },
            {
                "source": "hw/hw01.pdf",
                "category": "practice",
                "task_name": "homework",
                "sequence_name": "1",
                "category_depth": 1,
                "final_path": "practice/homework/1/hw01.pdf",
            },
        ],
    }


@pytest.fixture()
def minimal_bfs_tree_doc() -> Dict[str, Any]:
    return {
        "path": ".",
        "name": "root",
        "type": "folder",
        "children": {
            "lec": {
                "path": "lec",
                "name": "lec",
                "type": "folder",
                "category": "study",
                "files": {
                    "abc123": {
                        "path": "lec/lec01.pdf",
                        "name": "lec01.pdf",
                        "type": "file",
                        "file_hash": "abc123",
                    }
                },
            }
        },
    }


# ---------------------------------------------------------------------------
# OrchestratorConfig tests
# ---------------------------------------------------------------------------

class TestOrchestratorConfig:
    def test_defaults(self, tmp_project: Path) -> None:
        cfg = OrchestratorConfig(
            source_dir=str(tmp_project / "course"),
            db_path=str(tmp_project / "input" / "course_metadata.db"),
        )
        assert cfg.run_mode == RunMode.BFS_AND_REARRANGE
        assert cfg.eval_enabled is False
        assert cfg.multi_match is True
        assert cfg.bfs_model == "gpt-5-mini-2025-08-07"

    def test_eval_mode_requires_ground_truth(self, tmp_project: Path) -> None:
        with pytest.raises(ValueError, match="ground_truth_path"):
            OrchestratorConfig(
                source_dir=str(tmp_project / "course"),
                db_path=str(tmp_project / "input" / "course_metadata.db"),
                eval_enabled=True,
                ground_truth_path=None,
            )

    def test_eval_mode_with_ground_truth(self, tmp_project: Path) -> None:
        cfg = OrchestratorConfig(
            source_dir=str(tmp_project / "course"),
            db_path=str(tmp_project / "input" / "course_metadata.db"),
            eval_enabled=True,
            ground_truth_path=str(tmp_project / "gt"),
        )
        assert cfg.eval_enabled is True

    def test_rearrange_only_mode(self, tmp_project: Path) -> None:
        cfg = OrchestratorConfig(
            source_dir=str(tmp_project / "course"),
            db_path=str(tmp_project / "input" / "course_metadata.db"),
            run_mode=RunMode.REARRANGE_ONLY,
            bfs_final_paths=str(tmp_project / "input" / "bfs_v4_final_paths.json"),
        )
        assert cfg.run_mode == RunMode.REARRANGE_ONLY

    def test_rearrange_only_requires_final_paths(self, tmp_project: Path) -> None:
        with pytest.raises(ValueError, match="bfs_final_paths"):
            OrchestratorConfig(
                source_dir=str(tmp_project / "course"),
                db_path=str(tmp_project / "input" / "course_metadata.db"),
                run_mode=RunMode.REARRANGE_ONLY,
                bfs_final_paths=None,
            )


# ---------------------------------------------------------------------------
# PipelineResult tests
# ---------------------------------------------------------------------------

class TestPipelineResult:
    def test_success_result(self) -> None:
        r = PipelineResult(success=True, final_tree_path="/out/tree.json")
        assert r.success is True
        assert r.eval_report is None

    def test_failed_result_carries_error(self) -> None:
        r = PipelineResult(success=False, error="something broke")
        assert r.success is False
        assert "broke" in r.error

    def test_result_with_eval(self) -> None:
        report = {"f1": 0.87}
        r = PipelineResult(success=True, final_tree_path="/out/tree.json", eval_report=report)
        assert r.eval_report["f1"] == pytest.approx(0.87)


# ---------------------------------------------------------------------------
# run_bfs_stage tests
# ---------------------------------------------------------------------------

class TestRunBfsStage:
    def test_calls_bfs_reorganize_v5_with_correct_args(
        self, tmp_project: Path
    ) -> None:
        fake_traversal = MagicMock()
        fake_traversal.mappings = {}
        fake_traversal.missing_from_db = []
        fake_traversal.files_on_disk_count = 0

        cfg = OrchestratorConfig(
            source_dir=str(tmp_project / "course"),
            db_path=str(tmp_project / "input" / "course_metadata.db"),
            output_dir=str(tmp_project / "outputs"),
        )

        with patch("pipeline_orchestrator.bfs_reorganize_v5", return_value=fake_traversal) as mock_bfs:
            run_bfs_stage(cfg)

        mock_bfs.assert_called_once()
        call_kwargs = mock_bfs.call_args[1]
        assert call_kwargs["course_root"] == str(tmp_project / "course")
        assert call_kwargs["db_path"] == str(tmp_project / "input" / "course_metadata.db")

    def test_returns_bfs_stage_result(self, tmp_project: Path) -> None:
        fake_traversal = MagicMock()
        fake_traversal.mappings = {}
        fake_traversal.missing_from_db = []
        fake_traversal.files_on_disk_count = 0

        cfg = OrchestratorConfig(
            source_dir=str(tmp_project / "course"),
            db_path=str(tmp_project / "input" / "course_metadata.db"),
            output_dir=str(tmp_project / "outputs"),
        )

        with patch("pipeline_orchestrator.bfs_reorganize_v5", return_value=fake_traversal):
            result = run_bfs_stage(cfg)

        assert isinstance(result, BfsStageResult)
        assert result.traversal_result is fake_traversal
        assert result.final_paths_json_path is not None

    def test_raises_on_bfs_failure(self, tmp_project: Path) -> None:
        cfg = OrchestratorConfig(
            source_dir=str(tmp_project / "course"),
            db_path=str(tmp_project / "input" / "course_metadata.db"),
            output_dir=str(tmp_project / "outputs"),
        )

        with patch("pipeline_orchestrator.bfs_reorganize_v5", side_effect=RuntimeError("BFS failed")):
            with pytest.raises(RuntimeError, match="BFS failed"):
                run_bfs_stage(cfg)


# ---------------------------------------------------------------------------
# run_rearrange_stage tests
# ---------------------------------------------------------------------------

class TestRunRearrangeStage:
    def test_calls_run_pipeline_cli(
        self, tmp_project: Path, minimal_final_paths_doc: Dict
    ) -> None:
        final_paths_file = tmp_project / "input" / "bfs_v4_final_paths.json"
        final_paths_file.write_text(json.dumps(minimal_final_paths_doc))

        cfg = OrchestratorConfig(
            source_dir=str(tmp_project / "course"),
            db_path=str(tmp_project / "input" / "course_metadata.db"),
            output_dir=str(tmp_project / "outputs"),
            bfs_final_paths=str(final_paths_file),
            run_mode=RunMode.REARRANGE_ONLY,
        )

        fake_tree = [{"type": "group", "name": "Study", "children": []}]
        tree_out = tmp_project / "outputs" / "rearrangement_structure_tree.json"
        tree_out.parent.mkdir(parents=True, exist_ok=True)
        tree_out.write_text(json.dumps(fake_tree))

        with patch("pipeline_orchestrator.run_pipeline_cli") as mock_cli:
            result_path = run_rearrange_stage(cfg, final_paths_path=str(final_paths_file))

        mock_cli.assert_called_once()

    def test_returns_path_to_structure_tree(
        self, tmp_project: Path, minimal_final_paths_doc: Dict
    ) -> None:
        final_paths_file = tmp_project / "input" / "bfs_v4_final_paths.json"
        final_paths_file.write_text(json.dumps(minimal_final_paths_doc))

        cfg = OrchestratorConfig(
            source_dir=str(tmp_project / "course"),
            db_path=str(tmp_project / "input" / "course_metadata.db"),
            output_dir=str(tmp_project / "outputs"),
            bfs_final_paths=str(final_paths_file),
            run_mode=RunMode.REARRANGE_ONLY,
        )

        fake_tree = [{"type": "group", "name": "Study", "children": []}]
        tree_out = tmp_project / "outputs" / "rearrangement_structure_tree.json"
        tree_out.parent.mkdir(parents=True, exist_ok=True)
        tree_out.write_text(json.dumps(fake_tree))

        with patch("pipeline_orchestrator.run_pipeline_cli"):
            result_path = run_rearrange_stage(cfg, final_paths_path=str(final_paths_file))

        assert result_path is not None
        assert Path(result_path).exists()

    def test_raises_when_tree_not_produced(
        self, tmp_project: Path, minimal_final_paths_doc: Dict
    ) -> None:
        final_paths_file = tmp_project / "input" / "bfs_v4_final_paths.json"
        final_paths_file.write_text(json.dumps(minimal_final_paths_doc))

        cfg = OrchestratorConfig(
            source_dir=str(tmp_project / "course"),
            db_path=str(tmp_project / "input" / "course_metadata.db"),
            output_dir=str(tmp_project / "outputs"),
            bfs_final_paths=str(final_paths_file),
            run_mode=RunMode.REARRANGE_ONLY,
        )

        with patch("pipeline_orchestrator.run_pipeline_cli"):
            # No tree file written → should raise
            with pytest.raises(FileNotFoundError):
                run_rearrange_stage(cfg, final_paths_path=str(final_paths_file))


# ---------------------------------------------------------------------------
# run_evaluation_stage tests
# ---------------------------------------------------------------------------

class TestRunEvaluationStage:
    def test_skipped_when_eval_disabled(self, tmp_project: Path) -> None:
        cfg = OrchestratorConfig(
            source_dir=str(tmp_project / "course"),
            db_path=str(tmp_project / "input" / "course_metadata.db"),
            eval_enabled=False,
        )
        result = run_evaluation_stage(cfg, prediction_path="/irrelevant/path.json")
        assert result is None

    def test_runs_evaluation_when_enabled(
        self, tmp_project: Path, minimal_final_paths_doc: Dict
    ) -> None:
        gt_dir = tmp_project / "gt"
        gt_dir.mkdir()

        prediction_file = tmp_project / "outputs" / "bfs_v4_final_paths.json"
        prediction_file.parent.mkdir(parents=True, exist_ok=True)
        prediction_file.write_text(json.dumps(minimal_final_paths_doc))

        cfg = OrchestratorConfig(
            source_dir=str(tmp_project / "course"),
            db_path=str(tmp_project / "input" / "course_metadata.db"),
            eval_enabled=True,
            ground_truth_path=str(gt_dir),
            eval_method="top-down",
            eval_limit=3,
        )

        fake_report = {"folder": ".", "precision": 0.9, "recall": 0.8, "f1": 0.85}

        with patch("pipeline_orchestrator.run_evaluation", return_value=fake_report) as mock_eval:
            report = run_evaluation_stage(cfg, prediction_path=str(prediction_file))

        mock_eval.assert_called_once()
        assert report["f1"] == pytest.approx(0.85)

    def test_evaluation_report_saved_to_disk(
        self, tmp_project: Path, minimal_final_paths_doc: Dict
    ) -> None:
        gt_dir = tmp_project / "gt"
        gt_dir.mkdir()

        prediction_file = tmp_project / "outputs" / "bfs_v4_final_paths.json"
        prediction_file.parent.mkdir(parents=True, exist_ok=True)
        prediction_file.write_text(json.dumps(minimal_final_paths_doc))

        cfg = OrchestratorConfig(
            source_dir=str(tmp_project / "course"),
            db_path=str(tmp_project / "input" / "course_metadata.db"),
            eval_enabled=True,
            ground_truth_path=str(gt_dir),
            eval_output_dir=str(tmp_project / "eval_reports"),
        )

        fake_report = {"folder": ".", "f1": 0.7}

        with patch("pipeline_orchestrator.run_evaluation", return_value=fake_report):
            run_evaluation_stage(cfg, prediction_path=str(prediction_file))

        report_files = list((tmp_project / "eval_reports").glob("*.json"))
        assert len(report_files) == 1
        saved = json.loads(report_files[0].read_text())
        assert saved["f1"] == pytest.approx(0.7)


# ---------------------------------------------------------------------------
# orchestrate() end-to-end tests (mocked at stage boundaries)
# ---------------------------------------------------------------------------

class TestOrchestrate:
    def test_bfs_and_rearrange_mode(
        self, tmp_project: Path, minimal_final_paths_doc: Dict
    ) -> None:
        cfg = OrchestratorConfig(
            source_dir=str(tmp_project / "course"),
            db_path=str(tmp_project / "input" / "course_metadata.db"),
            output_dir=str(tmp_project / "outputs"),
            run_mode=RunMode.BFS_AND_REARRANGE,
        )

        fake_bfs_result = BfsStageResult(
            traversal_result=MagicMock(),
            final_paths_json_path=str(tmp_project / "outputs" / "bfs_v5_final_paths.json"),
            missing_from_db=[],
        )
        rearrange_path = str(tmp_project / "outputs" / "rearrangement_structure_tree.json")

        with (
            patch("pipeline_orchestrator.run_bfs_stage", return_value=fake_bfs_result) as mock_bfs,
            patch("pipeline_orchestrator.run_rearrange_stage", return_value=rearrange_path) as mock_rearr,
            patch("pipeline_orchestrator.run_evaluation_stage", return_value=None),
        ):
            result = orchestrate(cfg)

        assert result.success is True
        assert result.final_tree_path == rearrange_path
        mock_bfs.assert_called_once_with(cfg)
        mock_rearr.assert_called_once()

    def test_rearrange_only_mode_skips_bfs(
        self, tmp_project: Path, minimal_final_paths_doc: Dict
    ) -> None:
        fp = tmp_project / "input" / "bfs_v4_final_paths.json"
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(json.dumps(minimal_final_paths_doc))

        cfg = OrchestratorConfig(
            source_dir=str(tmp_project / "course"),
            db_path=str(tmp_project / "input" / "course_metadata.db"),
            output_dir=str(tmp_project / "outputs"),
            run_mode=RunMode.REARRANGE_ONLY,
            bfs_final_paths=str(fp),
        )

        rearrange_path = str(tmp_project / "outputs" / "rearrangement_structure_tree.json")

        with (
            patch("pipeline_orchestrator.run_bfs_stage") as mock_bfs,
            patch("pipeline_orchestrator.run_rearrange_stage", return_value=rearrange_path),
            patch("pipeline_orchestrator.run_evaluation_stage", return_value=None),
        ):
            result = orchestrate(cfg)

        mock_bfs.assert_not_called()
        assert result.success is True

    def test_bfs_only_mode_skips_rearrange(self, tmp_project: Path) -> None:
        cfg = OrchestratorConfig(
            source_dir=str(tmp_project / "course"),
            db_path=str(tmp_project / "input" / "course_metadata.db"),
            output_dir=str(tmp_project / "outputs"),
            run_mode=RunMode.BFS_ONLY,
        )

        fp = str(tmp_project / "outputs" / "bfs_v5_final_paths.json")
        fake_bfs_result = BfsStageResult(
            traversal_result=MagicMock(),
            final_paths_json_path=fp,
            missing_from_db=[],
        )

        with (
            patch("pipeline_orchestrator.run_bfs_stage", return_value=fake_bfs_result),
            patch("pipeline_orchestrator.run_rearrange_stage") as mock_rearr,
            patch("pipeline_orchestrator.run_evaluation_stage", return_value=None),
        ):
            result = orchestrate(cfg)

        mock_rearr.assert_not_called()
        assert result.success is True
        assert result.bfs_stage_result is fake_bfs_result

    def test_eval_report_included_in_result(
        self, tmp_project: Path, minimal_final_paths_doc: Dict
    ) -> None:
        fp = tmp_project / "input" / "bfs_v4_final_paths.json"
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(json.dumps(minimal_final_paths_doc))

        cfg = OrchestratorConfig(
            source_dir=str(tmp_project / "course"),
            db_path=str(tmp_project / "input" / "course_metadata.db"),
            output_dir=str(tmp_project / "outputs"),
            run_mode=RunMode.REARRANGE_ONLY,
            bfs_final_paths=str(fp),
            eval_enabled=True,
            ground_truth_path=str(tmp_project / "gt"),
        )

        fake_report = {"folder": ".", "f1": 0.92}
        rearrange_path = str(tmp_project / "outputs" / "rearrangement_structure_tree.json")

        with (
            patch("pipeline_orchestrator.run_bfs_stage"),
            patch("pipeline_orchestrator.run_rearrange_stage", return_value=rearrange_path),
            patch("pipeline_orchestrator.run_evaluation_stage", return_value=fake_report),
        ):
            result = orchestrate(cfg)

        assert result.eval_report is not None
        assert result.eval_report["f1"] == pytest.approx(0.92)

    def test_pipeline_failure_returns_failed_result(self, tmp_project: Path) -> None:
        cfg = OrchestratorConfig(
            source_dir=str(tmp_project / "course"),
            db_path=str(tmp_project / "input" / "course_metadata.db"),
            run_mode=RunMode.BFS_AND_REARRANGE,
        )

        with patch(
            "pipeline_orchestrator.run_bfs_stage",
            side_effect=RuntimeError("network timeout"),
        ):
            result = orchestrate(cfg)

        assert result.success is False
        assert "network timeout" in result.error

    def test_orchestrate_logs_stage_boundaries(
        self, tmp_project: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        cfg = OrchestratorConfig(
            source_dir=str(tmp_project / "course"),
            db_path=str(tmp_project / "input" / "course_metadata.db"),
            run_mode=RunMode.BFS_AND_REARRANGE,
        )

        bfs_paths = {
            "final_paths": str(tmp_project / "outputs" / "bfs_v4_final_paths.json"),
            "tree": str(tmp_project / "outputs" / "bfs_v4_tree.json"),
        }

        with caplog.at_level(logging.INFO, logger="pipeline_orchestrator"):
            with (
                patch("pipeline_orchestrator.run_bfs_stage", return_value=bfs_paths),
                patch(
                    "pipeline_orchestrator.run_rearrange_stage",
                    return_value=str(tmp_project / "out" / "tree.json"),
                ),
                patch("pipeline_orchestrator.run_evaluation_stage", return_value=None),
            ):
                orchestrate(cfg)

        log_text = "\n".join(caplog.messages)
        assert "BFS" in log_text or "bfs" in log_text.lower()
        assert "rearrange" in log_text.lower()
