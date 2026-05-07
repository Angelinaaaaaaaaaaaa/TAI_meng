"""
Failing tests (RED) for the five new requirements:

  1. EVAL_ONLY mode — orchestrate() and CLI can run evaluation without BFS/Rearrange.
  2. Zip support — ground_truth_path and source_dir may be .zip files; they get
     extracted to a temp dir transparently before use.
  3. Debug mode — intermediate JSON/log files (bfs_v4_plan.json, study_enriched.json,
     backbone_result.json, orphan_matches.json, debug/ logs) are ONLY written when
     cfg.debug=True; in normal mode outputs/ contains only the final artifacts.
  4. In-memory handoff — BFS stage returns a TraversalResult object; rearrange stage
     consumes it directly without ever reading bfs_v4_final_paths.json from disk.
     The final_paths JSON is still WRITTEN for human inspection but is NOT read back.
  5. bfs_v5 — new bfs_v5.bfs_reorganize_v5() that tracks files present on disk
     but absent from the metadata DB, and includes them in a "missing_from_db"
     summary section; the orchestrator uses bfs_v5 instead of bfs_v4.
"""

import json
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ---- imports that will fail until we implement them (RED phase) -------------
from pipeline_orchestrator import (
    OrchestratorConfig,
    PipelineResult,
    RunMode,
    orchestrate,
    run_bfs_stage,
    run_evaluation_stage,
    run_rearrange_stage,
)


# ===========================================================================
# Requirement 1 — EVAL_ONLY mode
# ===========================================================================

class TestEvalOnlyMode:
    def test_run_mode_enum_has_eval_only(self):
        assert RunMode.EVAL_ONLY.value == "eval_only"

    def test_eval_only_requires_ground_truth(self, tmp_path):
        with pytest.raises(ValueError, match="ground_truth_path"):
            OrchestratorConfig(
                source_dir=str(tmp_path / "c"),
                db_path=str(tmp_path / "m.db"),
                run_mode=RunMode.EVAL_ONLY,
                ground_truth_path=None,
            )

    def test_eval_only_requires_prediction_path(self, tmp_path):
        with pytest.raises(ValueError, match="bfs_final_paths"):
            OrchestratorConfig(
                source_dir=str(tmp_path / "c"),
                db_path=str(tmp_path / "m.db"),
                run_mode=RunMode.EVAL_ONLY,
                ground_truth_path=str(tmp_path / "gt"),
                bfs_final_paths=None,
            )

    def test_eval_only_skips_bfs_and_rearrange(self, tmp_path):
        fp = tmp_path / "pred.json"
        fp.write_text(json.dumps({"all_final_paths": []}))

        cfg = OrchestratorConfig(
            source_dir=str(tmp_path / "c"),
            db_path=str(tmp_path / "m.db"),
            run_mode=RunMode.EVAL_ONLY,
            ground_truth_path=str(tmp_path / "gt"),
            bfs_final_paths=str(fp),
            eval_enabled=True,
        )

        fake_report = {"f1": 0.80}
        with (
            patch("pipeline_orchestrator.run_bfs_stage") as mock_bfs,
            patch("pipeline_orchestrator.run_rearrange_stage") as mock_rearr,
            patch("pipeline_orchestrator.run_evaluation_stage", return_value=fake_report),
        ):
            result = orchestrate(cfg)

        mock_bfs.assert_not_called()
        mock_rearr.assert_not_called()
        assert result.success is True
        assert result.eval_report["f1"] == pytest.approx(0.80)

    def test_eval_only_result_has_no_tree_path(self, tmp_path):
        fp = tmp_path / "pred.json"
        fp.write_text(json.dumps({"all_final_paths": []}))

        cfg = OrchestratorConfig(
            source_dir=str(tmp_path / "c"),
            db_path=str(tmp_path / "m.db"),
            run_mode=RunMode.EVAL_ONLY,
            ground_truth_path=str(tmp_path / "gt"),
            bfs_final_paths=str(fp),
            eval_enabled=True,
        )

        with (
            patch("pipeline_orchestrator.run_bfs_stage"),
            patch("pipeline_orchestrator.run_rearrange_stage"),
            patch("pipeline_orchestrator.run_evaluation_stage", return_value={"f1": 0.5}),
        ):
            result = orchestrate(cfg)

        assert result.final_tree_path is None
        assert result.bfs_paths is None

    def test_cli_eval_only_mode(self, tmp_path, capsys):
        import pipeline_orchestrator as orch

        fp = tmp_path / "pred.json"
        fp.write_text("{}")
        fake_result = PipelineResult(
            success=True,
            eval_report={"f1": 0.91},
        )
        with patch("pipeline_orchestrator.orchestrate", return_value=fake_result):
            orch.main([
                "--source", str(tmp_path / "c"),
                "--db", str(tmp_path / "m.db"),
                "--mode", "eval_only",
                "--bfs-final-paths", str(fp),
                "--eval",
                "--ground-truth", str(tmp_path / "gt"),
            ])

        out = capsys.readouterr().out
        assert "0.91" in out


# ===========================================================================
# Requirement 2 — Zip support for ground_truth_path and source_dir
# ===========================================================================

class TestZipSupport:
    def _make_zip(self, tmp_path: Path, name: str, files: dict) -> Path:
        """Create a zip at tmp_path/name containing files dict {rel_path: content}."""
        zp = tmp_path / name
        with zipfile.ZipFile(zp, "w") as zf:
            for rel, content in files.items():
                zf.writestr(rel, content)
        return zp

    def test_resolve_path_directory_unchanged(self, tmp_path):
        from pipeline_orchestrator import resolve_path_or_zip

        d = tmp_path / "mydir"
        d.mkdir()
        resolved, cleanup = resolve_path_or_zip(str(d))
        assert Path(resolved) == d
        assert cleanup is None

    def test_resolve_path_zip_extracts_to_temp(self, tmp_path):
        from pipeline_orchestrator import resolve_path_or_zip

        # zip has two top-level entries (no single-dir wrapper), so resolved = tempdir
        zp = self._make_zip(tmp_path, "data.zip", {"a/b.txt": "hello", "c.txt": "world"})
        resolved, cleanup = resolve_path_or_zip(str(zp))
        try:
            assert Path(resolved).is_dir()
            assert (Path(resolved) / "a" / "b.txt").exists()
        finally:
            if cleanup:
                cleanup()

    def test_cleanup_removes_temp_dir(self, tmp_path):
        from pipeline_orchestrator import resolve_path_or_zip

        zp = self._make_zip(tmp_path, "data.zip", {"file.txt": "x"})
        resolved, cleanup = resolve_path_or_zip(str(zp))
        extracted_dir = Path(resolved)
        assert extracted_dir.exists()
        cleanup()
        assert not extracted_dir.exists()

    def test_zip_source_dir_used_in_bfs_stage(self, tmp_path):
        """When source_dir is a zip, BFS should receive the extracted directory."""
        zp = self._make_zip(
            tmp_path,
            "course.zip",
            {"lec/lec01.pdf": "content", "hw/hw01.pdf": "content"},
        )
        cfg = OrchestratorConfig(
            source_dir=str(zp),
            db_path=str(tmp_path / "m.db"),
            output_dir=str(tmp_path / "out"),
            run_mode=RunMode.BFS_ONLY,
        )

        captured = {}

        def fake_bfs_reorganize_v5(**kwargs):
            root = kwargs.get("course_root")
            captured["course_root"] = root
            # Capture is_dir() while the temp dir still exists (context manager hasn't exited)
            captured["is_dir_at_call_time"] = Path(root).is_dir()
            return MagicMock(mappings={}, missing_from_db=[], files_on_disk_count=0)

        with patch("pipeline_orchestrator.bfs_reorganize_v5", side_effect=fake_bfs_reorganize_v5):
            run_bfs_stage(cfg)

        # Must NOT pass the .zip path directly
        assert not captured["course_root"].endswith(".zip")
        assert captured["is_dir_at_call_time"] is True

    def test_zip_ground_truth_used_in_eval(self, tmp_path):
        """When ground_truth_path is a zip, eval gets the extracted directory."""
        gt_zip = self._make_zip(
            tmp_path,
            "gt.zip",
            {"study/lec/lec01.pdf": "", "practice/hw/hw01.pdf": ""},
        )
        fp = tmp_path / "pred.json"
        fp.write_text(json.dumps({"all_final_paths": []}))

        cfg = OrchestratorConfig(
            source_dir=str(tmp_path / "c"),
            db_path=str(tmp_path / "m.db"),
            run_mode=RunMode.EVAL_ONLY,
            ground_truth_path=str(gt_zip),
            bfs_final_paths=str(fp),
            eval_enabled=True,
        )

        captured = {}

        def fake_run_evaluation(**kwargs):
            gt = kwargs.get("ground_truth_path")
            captured["ground_truth_path"] = gt
            # Check is_dir while temp dir still exists (before context manager exits)
            captured["is_dir_at_call_time"] = Path(gt).is_dir() if gt else False
            return {"f1": 0.5}

        with patch("pipeline_orchestrator.run_evaluation", side_effect=fake_run_evaluation):
            run_evaluation_stage(cfg, prediction_path=str(fp))

        assert captured["ground_truth_path"] is not None
        assert not captured["ground_truth_path"].endswith(".zip")
        assert captured["is_dir_at_call_time"] is True


# ===========================================================================
# Requirement 3 — Debug mode controls intermediate file output
# ===========================================================================

class TestDebugMode:
    def test_config_has_debug_field_default_false(self, tmp_path):
        cfg = OrchestratorConfig(
            source_dir=str(tmp_path / "c"),
            db_path=str(tmp_path / "m.db"),
        )
        assert cfg.debug is False

    def test_debug_true_accepted(self, tmp_path):
        cfg = OrchestratorConfig(
            source_dir=str(tmp_path / "c"),
            db_path=str(tmp_path / "m.db"),
            debug=True,
        )
        assert cfg.debug is True

    def test_debug_false_no_intermediate_files_written(self, tmp_path):
        """In non-debug mode, bfs_v4_plan.json / bfs_v4_report.md should NOT be written."""
        cfg = OrchestratorConfig(
            source_dir=str(tmp_path / "c"),
            db_path=str(tmp_path / "m.db"),
            output_dir=str(tmp_path / "out"),
            debug=False,
        )

        fake_result = MagicMock()
        fake_result.mappings = {}
        fake_result.missing_from_db = []
        fake_result.files_on_disk_count = 0

        with patch("pipeline_orchestrator.bfs_reorganize_v5", return_value=fake_result):
            run_bfs_stage(cfg)

        out = tmp_path / "out"
        intermediate_files = list(out.glob("bfs_v4_plan.json")) + list(out.glob("bfs_v4_report.md"))
        assert intermediate_files == [], f"Unexpected intermediate files: {intermediate_files}"

    def test_debug_true_intermediate_files_written(self, tmp_path):
        """In debug mode, plan/tree/report paths are passed non-None to bfs_reorganize_v5."""
        cfg = OrchestratorConfig(
            source_dir=str(tmp_path / "c"),
            db_path=str(tmp_path / "m.db"),
            output_dir=str(tmp_path / "out"),
            debug=True,
        )

        fake_result = MagicMock()
        fake_result.mappings = {}
        fake_result.missing_from_db = []
        fake_result.files_on_disk_count = 0

        captured = {}

        def fake_bfs(**kwargs):
            captured.update(kwargs)
            return fake_result

        with patch("pipeline_orchestrator.bfs_reorganize_v5", side_effect=fake_bfs):
            run_bfs_stage(cfg)

        assert captured.get("json_path") is not None
        assert captured.get("tree_path") is not None
        assert captured.get("report_path") is not None
        assert "v5" in (captured.get("json_path") or "").lower() or \
               "plan" in (captured.get("json_path") or "").lower()

    def test_debug_false_rearrange_no_debug_subdir(self, tmp_path):
        """In non-debug mode, the rearrange debug/ directory should NOT be created."""
        fp = tmp_path / "fp.json"
        fp.write_text(json.dumps({"all_final_paths": []}))

        cfg = OrchestratorConfig(
            source_dir=str(tmp_path / "c"),
            db_path=str(tmp_path / "m.db"),
            output_dir=str(tmp_path / "out"),
            bfs_final_paths=str(fp),
            run_mode=RunMode.REARRANGE_ONLY,
            debug=False,
        )

        tree_out = tmp_path / "out" / "rearrangement_structure_tree.json"
        tree_out.parent.mkdir(parents=True, exist_ok=True)
        tree_out.write_text("[]")

        captured_args = {}

        def fake_run_pipeline_cli(args, **kwargs):
            captured_args["args"] = args

        with patch("pipeline_orchestrator.run_pipeline_cli", side_effect=fake_run_pipeline_cli):
            run_rearrange_stage(cfg, final_paths_path=str(fp))

        # The args passed to run_pipeline_cli should signal no-debug
        args_ns = captured_args.get("args")
        assert args_ns is not None
        assert getattr(args_ns, "debug", False) is False

    def test_debug_cli_flag(self, tmp_path, capsys):
        import pipeline_orchestrator as orch

        fake_result = PipelineResult(success=True, final_tree_path="/out/tree.json")
        with patch("pipeline_orchestrator.orchestrate", return_value=fake_result) as mock_orch:
            orch.main([
                "--source", str(tmp_path / "c"),
                "--db", str(tmp_path / "m.db"),
                "--debug",
            ])

        cfg_used = mock_orch.call_args[0][0]
        assert cfg_used.debug is True


# ===========================================================================
# Requirement 4 — In-memory handoff (BFS → Rearrange, no file re-read)
# ===========================================================================

class TestInMemoryHandoff:
    def test_run_bfs_stage_returns_traversal_result(self, tmp_path):
        """run_bfs_stage must return a BfsStageResult that includes the TraversalResult object."""
        from pipeline_orchestrator import BfsStageResult

        cfg = OrchestratorConfig(
            source_dir=str(tmp_path / "c"),
            db_path=str(tmp_path / "m.db"),
            output_dir=str(tmp_path / "out"),
        )

        fake_traversal = MagicMock()
        fake_traversal.mappings = {}
        fake_traversal.missing_from_db = []
        fake_traversal.files_on_disk_count = 0

        with patch("pipeline_orchestrator.bfs_reorganize_v5", return_value=fake_traversal):
            result = run_bfs_stage(cfg)

        assert isinstance(result, BfsStageResult)
        assert result.traversal_result is fake_traversal

    def test_run_rearrange_stage_accepts_traversal_result(self, tmp_path):
        """run_rearrange_stage can accept a TraversalResult instead of a file path."""
        from pipeline_orchestrator import BfsStageResult

        fp = tmp_path / "fp.json"
        fp.write_text(json.dumps({"all_final_paths": []}))

        cfg = OrchestratorConfig(
            source_dir=str(tmp_path / "c"),
            db_path=str(tmp_path / "m.db"),
            output_dir=str(tmp_path / "out"),
            bfs_final_paths=str(fp),
            run_mode=RunMode.REARRANGE_ONLY,
        )

        tree_out = tmp_path / "out" / "rearrangement_structure_tree.json"
        tree_out.parent.mkdir(parents=True, exist_ok=True)
        tree_out.write_text("[]")

        fake_traversal = MagicMock()
        fake_traversal.mappings = {}

        bfs_result = BfsStageResult(
            traversal_result=fake_traversal,
            final_paths_json_path=str(fp),
            tree_json_path=None,
        )

        captured_args = {}

        def fake_run_pipeline_cli(args, **kwargs):
            captured_args["final_paths_doc"] = getattr(args, "final_paths_doc", None)

        with patch("pipeline_orchestrator.run_pipeline_cli", side_effect=fake_run_pipeline_cli):
            run_rearrange_stage(cfg, bfs_stage_result=bfs_result)

    def test_rearrange_stage_does_not_open_json_file(self, tmp_path):
        """When a BfsStageResult is passed, run_rearrange_stage must NOT open the JSON file."""
        from pipeline_orchestrator import BfsStageResult

        fp = tmp_path / "fp.json"
        fp.write_text(json.dumps({"all_final_paths": []}))

        cfg = OrchestratorConfig(
            source_dir=str(tmp_path / "c"),
            db_path=str(tmp_path / "m.db"),
            output_dir=str(tmp_path / "out"),
            bfs_final_paths=str(fp),
            run_mode=RunMode.REARRANGE_ONLY,
        )

        tree_out = tmp_path / "out" / "rearrangement_structure_tree.json"
        tree_out.parent.mkdir(parents=True, exist_ok=True)
        tree_out.write_text("[]")

        fake_traversal = MagicMock()
        bfs_result = BfsStageResult(
            traversal_result=fake_traversal,
            final_paths_json_path=str(fp),
            tree_json_path=None,
        )

        # Rename the file so any attempt to open it fails
        fp.rename(tmp_path / "fp_GONE.json")

        with patch("pipeline_orchestrator.run_pipeline_cli"):
            # Should NOT raise even though fp no longer exists
            run_rearrange_stage(cfg, bfs_stage_result=bfs_result)

    def test_orchestrate_passes_traversal_result_through(self, tmp_path):
        """orchestrate() passes the TraversalResult from BFS directly to Rearrange."""
        from pipeline_orchestrator import BfsStageResult

        cfg = OrchestratorConfig(
            source_dir=str(tmp_path / "c"),
            db_path=str(tmp_path / "m.db"),
            output_dir=str(tmp_path / "out"),
            run_mode=RunMode.BFS_AND_REARRANGE,
        )

        fake_traversal = MagicMock()
        bfs_result = BfsStageResult(
            traversal_result=fake_traversal,
            final_paths_json_path=str(tmp_path / "fp.json"),
            tree_json_path=None,
        )

        rearr_call_kwargs = {}

        def fake_rearrange(cfg_arg, *, bfs_stage_result=None, **kw):
            rearr_call_kwargs["bfs_stage_result"] = bfs_stage_result
            return str(tmp_path / "out" / "tree.json")

        with (
            patch("pipeline_orchestrator.run_bfs_stage", return_value=bfs_result),
            patch("pipeline_orchestrator.run_rearrange_stage", side_effect=fake_rearrange),
            patch("pipeline_orchestrator.run_evaluation_stage", return_value=None),
        ):
            orchestrate(cfg)

        assert rearr_call_kwargs["bfs_stage_result"] is bfs_result
        assert rearr_call_kwargs["bfs_stage_result"].traversal_result is fake_traversal


# ===========================================================================
# Requirement 5 — bfs_v5: missing-from-db tracking
# ===========================================================================

class TestBfsV5:
    def test_bfs_v5_module_importable(self):
        import bfs_v5  # noqa: F401

    def test_bfs_reorganize_v5_exists(self):
        from bfs_v5 import bfs_reorganize_v5
        assert callable(bfs_reorganize_v5)

    def test_traversal_result_has_missing_from_db(self):
        from bfs_v5 import TraversalResult as V5TraversalResult
        tr = V5TraversalResult(
            classifications={},
            folder_decisions={},
            skipped_folders=[],
            mappings={},
            files_classified_individually=0,
            files_classified_via_folder=0,
            missing_from_db=["a.pdf", "b.pdf"],
        )
        assert tr.missing_from_db == ["a.pdf", "b.pdf"]

    def test_missing_from_db_reflects_files_not_in_db(self, tmp_path):
        """Files on disk that have no DB entry must appear in missing_from_db."""
        from bfs_v5 import CourseDB, BFSTraverser
        from classify_v4 import LLMClassifier

        # Create a course directory with one file that has no DB metadata
        course = tmp_path / "course"
        (course / "lec").mkdir(parents=True)
        (course / "lec" / "lec01.pdf").write_bytes(b"pdf")

        db = CourseDB(":memory_mock:")  # we'll patch db.load_file_index
        db.conn = MagicMock()

        mock_classifier = MagicMock(spec=LLMClassifier)
        traverser = BFSTraverser(db, mock_classifier)

        # file_index is empty → lec01.pdf has no DB entry
        with patch.object(db, "load_file_index", return_value={}):
            with patch.object(db, "connect"):
                with patch.object(mock_classifier, "classify_folder") as mock_cf:
                    mock_cf.return_value = MagicMock(
                        category=MagicMock(value="study"),
                        is_mixed=False,
                        by_type=False,
                        reason="test",
                        folder_description="",
                        task_name=None,
                        sequence_name=None,
                        category_depth=0,
                        by_sequence=False,
                    )
                    with patch.object(mock_classifier, "refer_folder_task_sequence") as mock_rts:
                        mock_rts.return_value = MagicMock(
                            task_name=None, sequence_name=None, category_depth=0, by_type=False,
                        )
                        with patch.object(mock_classifier, "save_debug_log"):
                            result = traverser.traverse(str(course))

        assert "lec/lec01.pdf" in result.missing_from_db or \
               any("lec01.pdf" in p for p in result.missing_from_db)

    def test_summary_includes_missing_from_db_section(self, tmp_path):
        """The Markdown report generated by bfs_v5 must include a missing-from-db section."""
        from bfs_v5 import generate_report, TraversalResult

        fake_classifier = MagicMock()
        fake_classifier.debug_log = []

        tr = TraversalResult(
            classifications={},
            folder_decisions={},
            skipped_folders=[],
            mappings={},
            files_classified_individually=0,
            files_classified_via_folder=0,
            missing_from_db=["ghost.pdf", "phantom.pdf"],
            files_on_disk_count=3,
            files_missing_in_db=[],
            files_stale_in_db=[],
        )

        report_path = str(tmp_path / "report.md")
        generate_report(tr, fake_classifier, report_path)

        content = Path(report_path).read_text()
        assert "missing_from_db" in content.lower() or "Missing From DB" in content
        assert "ghost.pdf" in content
        assert "phantom.pdf" in content

    def test_orchestrator_imports_bfs_v5_not_v4(self):
        """pipeline_orchestrator must call bfs_reorganize_v5, not bfs_reorganize from bfs_v4."""
        import pipeline_orchestrator as orch
        import inspect

        src = inspect.getsource(orch)
        # Must reference v5
        assert "bfs_v5" in src or "bfs_reorganize_v5" in src
        # Must NOT call the old bfs_v4 entry point by name
        assert "from bfs_v4 import bfs_reorganize" not in src

    def test_bfs_stage_result_includes_missing_from_db(self, tmp_path):
        """BfsStageResult.missing_from_db must be populated from the traversal."""
        from pipeline_orchestrator import BfsStageResult

        cfg = OrchestratorConfig(
            source_dir=str(tmp_path / "c"),
            db_path=str(tmp_path / "m.db"),
            output_dir=str(tmp_path / "out"),
        )

        fake_traversal = MagicMock()
        fake_traversal.mappings = {}
        fake_traversal.missing_from_db = ["orphan.pdf"]
        fake_traversal.files_on_disk_count = 1

        with patch("pipeline_orchestrator.bfs_reorganize_v5", return_value=fake_traversal):
            result = run_bfs_stage(cfg)

        assert isinstance(result, BfsStageResult)
        assert "orphan.pdf" in result.missing_from_db
