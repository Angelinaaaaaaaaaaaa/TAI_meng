"""Additional tests to reach 80%+ coverage on pipeline_orchestrator.py.

Covers:
  - run_evaluation() internal dispatch (_run_evaluation_impl)
  - run_rearrange_stage() sub-directory tree fallback
  - orchestrate() BFS_ONLY with eval
  - main() CLI entry point (success + failure paths)
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pipeline_orchestrator as orch
from pipeline_orchestrator import (
    BfsStageResult,
    OrchestratorConfig,
    PipelineResult,
    RunMode,
    _run_evaluation_impl,
    orchestrate,
    run_bfs_stage,
    run_evaluation_stage,
    run_rearrange_stage,
)


# ---------------------------------------------------------------------------
# _run_evaluation_impl
# ---------------------------------------------------------------------------

class TestRunEvaluationImpl:
    def test_calls_all_helpers_in_order(self, tmp_path: Path) -> None:
        gt_dir = tmp_path / "gt"
        gt_dir.mkdir()
        pred_file = tmp_path / "pred.json"
        pred_file.write_text("{}")

        fake_hashes = {Path("a/b.pdf"): "hash1"}
        fake_pred_tree = {"tree": "data"}
        fake_gt_tree = {}
        fake_report = {"f1": 0.5}

        mock_get_hashes = MagicMock(return_value=fake_hashes)
        mock_construct = MagicMock(return_value=fake_pred_tree)
        mock_create_folder = MagicMock()
        mock_normalize = MagicMock(side_effect=lambda p: p)
        mock_chdir = MagicMock()
        mock_chdir.return_value.__enter__ = MagicMock(return_value=None)
        mock_chdir.return_value.__exit__ = MagicMock(return_value=False)
        mock_evaluate_tree = MagicMock(return_value=fake_report)

        result = _run_evaluation_impl(
            evaluate_tree=mock_evaluate_tree,
            get_ground_truth_hashes=mock_get_hashes,
            construct_tree_from_json=mock_construct,
            create_folder_children_dict=mock_create_folder,
            normalize_db_path_eval=mock_normalize,
            chdir=mock_chdir,
            db_path=str(tmp_path / "meta.db"),
            ground_truth_path=str(gt_dir),
            prediction_path=str(pred_file),
            method="top-down",
            limit=3,
        )

        mock_get_hashes.assert_called_once_with(str(tmp_path / "meta.db"))
        mock_construct.assert_called_once_with(fake_hashes, str(pred_file))
        mock_chdir.assert_called_once_with(str(gt_dir))
        mock_evaluate_tree.assert_called_once()
        assert result == fake_report

    def test_passes_method_and_limit(self, tmp_path: Path) -> None:
        gt_dir = tmp_path / "gt"
        gt_dir.mkdir()

        mock_chdir = MagicMock()
        mock_chdir.return_value.__enter__ = MagicMock(return_value=None)
        mock_chdir.return_value.__exit__ = MagicMock(return_value=False)
        captured_kwargs = {}

        def fake_evaluate_tree(gt, pred, method, limit):
            captured_kwargs["method"] = method
            captured_kwargs["limit"] = limit
            return {}

        _run_evaluation_impl(
            evaluate_tree=fake_evaluate_tree,
            get_ground_truth_hashes=MagicMock(return_value={}),
            construct_tree_from_json=MagicMock(return_value={}),
            create_folder_children_dict=MagicMock(),
            normalize_db_path_eval=MagicMock(side_effect=lambda p: p),
            chdir=mock_chdir,
            db_path="fake.db",
            ground_truth_path=str(gt_dir),
            prediction_path="pred.json",
            method="bottom-up",
            limit=5,
        )

        assert captured_kwargs["method"] == "bottom-up"
        assert captured_kwargs["limit"] == 5


# ---------------------------------------------------------------------------
# run_rearrange_stage – sub-directory tree path fallback
# ---------------------------------------------------------------------------

class TestRearrangeSubdirFallback:
    def test_finds_tree_in_course_subdir(self, tmp_path: Path) -> None:
        """Tree written under outputs/<course>/ should be found."""
        fp_file = tmp_path / "input" / "fp.json"
        fp_file.parent.mkdir()
        fp_file.write_text(json.dumps({"all_final_paths": []}))

        cfg = OrchestratorConfig(
            source_dir=str(tmp_path / "course"),
            db_path=str(tmp_path / "input" / "my_course_metadata.db"),
            output_dir=str(tmp_path / "outputs"),
            bfs_final_paths=str(fp_file),
            run_mode=RunMode.REARRANGE_ONLY,
            course_name="my_course",
        )

        # Simulate rearrange writing under outputs/my_course/
        subdir_tree = tmp_path / "outputs" / "my_course" / "rearrangement_structure_tree.json"
        subdir_tree.parent.mkdir(parents=True)
        subdir_tree.write_text(json.dumps([]))

        with patch("pipeline_orchestrator.run_pipeline_cli"):
            result = run_rearrange_stage(cfg, final_paths_path=str(fp_file))

        assert Path(result) == subdir_tree

    def test_course_name_derived_from_db_path(self, tmp_path: Path) -> None:
        """When course_name is None, it should be derived from the db filename."""
        fp_file = tmp_path / "fp.json"
        fp_file.write_text(json.dumps({"all_final_paths": []}))

        cfg = OrchestratorConfig(
            source_dir=str(tmp_path / "course"),
            db_path=str(tmp_path / "EECS_106B_metadata.db"),
            output_dir=str(tmp_path / "outputs"),
            bfs_final_paths=str(fp_file),
            run_mode=RunMode.REARRANGE_ONLY,
            course_name=None,
        )

        flat_tree = tmp_path / "outputs" / "rearrangement_structure_tree.json"
        flat_tree.parent.mkdir(parents=True)
        flat_tree.write_text(json.dumps([]))

        with patch("pipeline_orchestrator.run_pipeline_cli") as mock_cli:
            run_rearrange_stage(cfg, final_paths_path=str(fp_file))

        # The args namespace passed to run_pipeline_cli should have a course set
        args_passed = mock_cli.call_args[0][0]
        assert args_passed.course is not None
        assert "EECS_106B" in args_passed.course or "EECS" in args_passed.course


# ---------------------------------------------------------------------------
# orchestrate() BFS_ONLY with eval enabled
# ---------------------------------------------------------------------------

class TestOrchestrateEdgeCases:
    def test_bfs_only_with_eval(self, tmp_path: Path) -> None:
        cfg = OrchestratorConfig(
            source_dir=str(tmp_path / "course"),
            db_path=str(tmp_path / "meta.db"),
            output_dir=str(tmp_path / "out"),
            run_mode=RunMode.BFS_ONLY,
            eval_enabled=True,
            ground_truth_path=str(tmp_path / "gt"),
        )

        fp = str(tmp_path / "out" / "bfs_v5_final_paths.json")
        fake_bfs_result = BfsStageResult(
            traversal_result=MagicMock(),
            final_paths_json_path=fp,
            missing_from_db=[],
        )
        fake_report = {"f1": 0.75}

        with (
            patch("pipeline_orchestrator.run_bfs_stage", return_value=fake_bfs_result),
            patch("pipeline_orchestrator.run_rearrange_stage") as mock_rearr,
            patch("pipeline_orchestrator.run_evaluation_stage", return_value=fake_report) as mock_eval,
        ):
            result = orchestrate(cfg)

        mock_rearr.assert_not_called()
        mock_eval.assert_called_once_with(cfg, prediction_path=fp)
        assert result.success is True
        assert result.eval_report["f1"] == pytest.approx(0.75)
        assert result.bfs_stage_result is fake_bfs_result
        assert result.final_tree_path is None

    def test_rearrange_only_eval_uses_cfg_final_paths(self, tmp_path: Path) -> None:
        fp = tmp_path / "bfs_v4_final_paths.json"
        fp.write_text(json.dumps({"all_final_paths": []}))

        cfg = OrchestratorConfig(
            source_dir=str(tmp_path / "course"),
            db_path=str(tmp_path / "meta.db"),
            output_dir=str(tmp_path / "out"),
            run_mode=RunMode.REARRANGE_ONLY,
            bfs_final_paths=str(fp),
            eval_enabled=True,
            ground_truth_path=str(tmp_path / "gt"),
        )

        tree_path = str(tmp_path / "out" / "tree.json")

        with (
            patch("pipeline_orchestrator.run_bfs_stage"),
            patch("pipeline_orchestrator.run_rearrange_stage", return_value=tree_path),
            patch("pipeline_orchestrator.run_evaluation_stage", return_value={"f1": 0.6}) as mock_eval,
        ):
            result = orchestrate(cfg)

        # Should evaluate against the pre-supplied bfs_final_paths (no bfs_paths from stage)
        call_kwargs = mock_eval.call_args[1]
        assert call_kwargs["prediction_path"] == str(fp)


# ---------------------------------------------------------------------------
# main() CLI entry point
# ---------------------------------------------------------------------------

class TestMain:
    def test_main_bfs_and_rearrange_success(self, tmp_path: Path, capsys) -> None:
        fake_result = PipelineResult(
            success=True,
            bfs_paths={
                "final_paths": str(tmp_path / "final_paths.json"),
                "tree": str(tmp_path / "tree.json"),
            },
            final_tree_path=str(tmp_path / "structure_tree.json"),
        )

        with patch("pipeline_orchestrator.orchestrate", return_value=fake_result):
            orch.main([
                "--source", str(tmp_path / "course"),
                "--db", str(tmp_path / "meta.db"),
                "--mode", "bfs_and_rearrange",
            ])

        out = capsys.readouterr().out
        assert "successfully" in out.lower()

    def test_main_rearrange_only_success(self, tmp_path: Path, capsys) -> None:
        fp = tmp_path / "fp.json"
        fp.write_text("{}")

        fake_result = PipelineResult(
            success=True,
            final_tree_path=str(tmp_path / "structure.json"),
        )

        with patch("pipeline_orchestrator.orchestrate", return_value=fake_result):
            orch.main([
                "--source", str(tmp_path / "course"),
                "--db", str(tmp_path / "meta.db"),
                "--mode", "rearrange_only",
                "--bfs-final-paths", str(fp),
            ])

        out = capsys.readouterr().out
        assert "successfully" in out.lower()

    def test_main_failure_exits_nonzero(self, tmp_path: Path) -> None:
        fake_result = PipelineResult(success=False, error="something exploded")

        with patch("pipeline_orchestrator.orchestrate", return_value=fake_result):
            with pytest.raises(SystemExit) as exc_info:
                orch.main([
                    "--source", str(tmp_path / "course"),
                    "--db", str(tmp_path / "meta.db"),
                ])
        assert exc_info.value.code != 0

    def test_main_config_error_exits_nonzero(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as exc_info:
            orch.main([
                "--source", str(tmp_path / "course"),
                "--db", str(tmp_path / "meta.db"),
                "--mode", "rearrange_only",
                # missing --bfs-final-paths
            ])
        assert exc_info.value.code != 0

    def test_main_with_eval_flag(self, tmp_path: Path, capsys) -> None:
        gt = tmp_path / "gt"
        gt.mkdir()
        fake_result = PipelineResult(
            success=True,
            final_tree_path=str(tmp_path / "tree.json"),
            eval_report={"f1": 0.88},
        )

        with patch("pipeline_orchestrator.orchestrate", return_value=fake_result):
            orch.main([
                "--source", str(tmp_path / "course"),
                "--db", str(tmp_path / "meta.db"),
                "--mode", "rearrange_only",
                "--bfs-final-paths", str(tmp_path / "fp.json"),
                "--eval",
                "--ground-truth", str(gt),
            ])

        out = capsys.readouterr().out
        assert "0.8800" in out or "0.88" in out
