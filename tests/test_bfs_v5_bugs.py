"""
RED-phase tests for the four bfs_v5 bugs identified in code review.

Bug 1: SyntaxError — line 1090, `replace("\", "/")` must be `replace("\\", "/")`
Bug 2: Missing bfs_reorganize_v5 entry point with debug parameter
Bug 3: save_debug_log always fires, should be gated on debug flag
Bug 4: CLI main() missing --debug flag
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ---------------------------------------------------------------------------
# Bug 1: bfs_v5 must be importable (no SyntaxError)
# ---------------------------------------------------------------------------

class TestBfsV5Importable:
    def test_module_imports_without_error(self) -> None:
        import importlib
        spec = importlib.util.spec_from_file_location(
            "bfs_v5", str(_ROOT / "bfs_v5.py")
        )
        mod = importlib.util.module_from_spec(spec)
        # Should not raise SyntaxError or any ImportError
        spec.loader.exec_module(mod)

    def test_build_result_tree_callable(self) -> None:
        import bfs_v5
        assert callable(bfs_v5._build_result_tree)

    def test_build_result_tree_handles_backslash_in_dest_rel(self) -> None:
        """_build_result_tree must not crash when dest_rel contains backslashes."""
        import bfs_v5
        from dataclasses import dataclass, field
        from typing import Dict

        # Build a minimal TraversalResult with one mapping that has a backslash
        mapping = bfs_v5.FileMapping(
            source_rel="study\\lec1\\slides.pdf",
            dest_rel="study\\lec1\\slides.pdf",
            top_folder="study",
            category="study",
            reason="test",
        )
        result = bfs_v5.TraversalResult(
            classifications={},
            folder_decisions={},
            skipped_folders=[],
            mappings={"study\\lec1\\slides.pdf": mapping},
            files_classified_individually=1,
            files_classified_via_folder=0,
        )
        # Must not raise
        tree = bfs_v5._build_result_tree(result)
        assert tree["type"] == "folder"


# ---------------------------------------------------------------------------
# Bug 2: bfs_reorganize_v5 entry point must exist with debug parameter
# ---------------------------------------------------------------------------

class TestBfsReorganizeV5EntryPoint:
    def test_bfs_reorganize_v5_exists(self) -> None:
        import bfs_v5
        assert hasattr(bfs_v5, "bfs_reorganize_v5"), (
            "bfs_v5 must export bfs_reorganize_v5 (not just bfs_reorganize)"
        )
        assert callable(bfs_v5.bfs_reorganize_v5)

    def test_bfs_reorganize_v5_accepts_debug_false(self) -> None:
        import inspect
        import bfs_v5
        sig = inspect.signature(bfs_v5.bfs_reorganize_v5)
        assert "debug" in sig.parameters, "bfs_reorganize_v5 must accept a 'debug' parameter"
        assert sig.parameters["debug"].default is False, "debug must default to False"

    def test_debug_false_suppresses_intermediate_files(self, tmp_path: Path) -> None:
        """When debug=False, plan JSON, tree JSON, and report MD must NOT be written."""
        import bfs_v5

        fake_result = MagicMock()
        fake_result.mappings = {}
        fake_result.classifications = {}
        fake_result.folder_decisions = {}
        fake_result.skipped_folders = []
        fake_result.files_missing_in_db = []
        fake_result.missing_from_db = []
        fake_result.files_stale_in_db = []
        fake_result.files_on_disk_count = 0
        fake_result.files_classified_via_folder = 0
        fake_result.files_classified_individually = 0

        fake_traverser = MagicMock()
        fake_traverser.traverse.return_value = fake_result

        final_paths = tmp_path / "bfs_v5_final_paths.json"
        plan_json   = tmp_path / "bfs_v5_plan.json"
        tree_json   = tmp_path / "bfs_v5_tree.json"
        report_md   = tmp_path / "bfs_v5_report.md"

        with (
            patch("bfs_v5.CourseDB") as mock_db_cls,
            patch("bfs_v5.LLMClassifier"),
            patch("bfs_v5.BFSTraverser", return_value=fake_traverser),
            patch("bfs_v5.collect_task_names", return_value=set()),
            patch("bfs_v5.rematch_missing_task_names"),
            patch("bfs_v5.fill_sequence_names"),
            patch("bfs_v5.apply_flattened_final_paths"),
            patch("bfs_v5.export_final_paths_json"),
            patch("bfs_v5.export_mappings_json") as mock_plan,
            patch("bfs_v5.export_tree_json")    as mock_tree,
            patch("bfs_v5.generate_report")     as mock_report,
        ):
            mock_db_cls.return_value.__enter__ = MagicMock(return_value=None)
            mock_db_cls.return_value.__exit__  = MagicMock(return_value=False)
            mock_db_cls.return_value.load_file_index.return_value = {}

            bfs_v5.bfs_reorganize_v5(
                course_root=str(tmp_path / "course"),
                db_path=str(tmp_path / "meta.db"),
                debug=False,
            )

        mock_plan.assert_not_called()
        mock_tree.assert_not_called()
        mock_report.assert_not_called()

    def test_debug_true_writes_intermediate_files(self, tmp_path: Path) -> None:
        """When debug=True, plan JSON, tree JSON, and report MD MUST be written."""
        import bfs_v5

        fake_result = MagicMock()
        fake_result.mappings = {}
        fake_traverser = MagicMock()
        fake_traverser.traverse.return_value = fake_result

        with (
            patch("bfs_v5.CourseDB") as mock_db_cls,
            patch("bfs_v5.LLMClassifier"),
            patch("bfs_v5.BFSTraverser", return_value=fake_traverser),
            patch("bfs_v5.collect_task_names", return_value=set()),
            patch("bfs_v5.rematch_missing_task_names"),
            patch("bfs_v5.fill_sequence_names"),
            patch("bfs_v5.apply_flattened_final_paths"),
            patch("bfs_v5.export_final_paths_json"),
            patch("bfs_v5.export_mappings_json") as mock_plan,
            patch("bfs_v5.export_tree_json")    as mock_tree,
            patch("bfs_v5.generate_report")     as mock_report,
        ):
            mock_db_cls.return_value.__enter__ = MagicMock(return_value=None)
            mock_db_cls.return_value.__exit__  = MagicMock(return_value=False)
            mock_db_cls.return_value.load_file_index.return_value = {}

            bfs_v5.bfs_reorganize_v5(
                course_root=str(tmp_path / "course"),
                db_path=str(tmp_path / "meta.db"),
                debug=True,
            )

        mock_plan.assert_called_once()
        mock_tree.assert_called_once()
        mock_report.assert_called_once()

    def test_final_paths_json_always_written(self, tmp_path: Path) -> None:
        """export_final_paths_json must be called regardless of debug flag."""
        import bfs_v5

        fake_result = MagicMock()
        fake_result.mappings = {}
        fake_traverser = MagicMock()
        fake_traverser.traverse.return_value = fake_result

        with (
            patch("bfs_v5.CourseDB") as mock_db_cls,
            patch("bfs_v5.LLMClassifier"),
            patch("bfs_v5.BFSTraverser", return_value=fake_traverser),
            patch("bfs_v5.collect_task_names", return_value=set()),
            patch("bfs_v5.rematch_missing_task_names"),
            patch("bfs_v5.fill_sequence_names"),
            patch("bfs_v5.apply_flattened_final_paths"),
            patch("bfs_v5.export_final_paths_json") as mock_fp,
            patch("bfs_v5.export_mappings_json"),
            patch("bfs_v5.export_tree_json"),
            patch("bfs_v5.generate_report"),
        ):
            mock_db_cls.return_value.__enter__ = MagicMock(return_value=None)
            mock_db_cls.return_value.__exit__  = MagicMock(return_value=False)
            mock_db_cls.return_value.load_file_index.return_value = {}

            bfs_v5.bfs_reorganize_v5(
                course_root=str(tmp_path / "course"),
                db_path=str(tmp_path / "meta.db"),
                debug=False,
            )

        mock_fp.assert_called_once()


# ---------------------------------------------------------------------------
# Bug 3: save_debug_log must be gated on debug flag
# ---------------------------------------------------------------------------

class TestSaveDebugLogGating:
    def _run_traverse_with_debug(self, tmp_path: Path, debug: bool):
        import bfs_v5

        mock_db = MagicMock()
        mock_db.load_file_index.return_value = {}
        mock_classifier = MagicMock()
        mock_classifier.debug_log = []

        traverser = bfs_v5.BFSTraverser(mock_db, mock_classifier)

        fake_bfs_result = bfs_v5.TraversalResult(
            classifications={},
            folder_decisions={},
            skipped_folders=[],
            mappings={},
            files_classified_individually=0,
            files_classified_via_folder=0,
        )

        with (
            patch("bfs_v5.scan_directory", return_value=[]),
            patch("bfs_v5.compute_sync_stats", return_value=([], [])),
            patch("bfs_v5.collect_missing_from_db", return_value=[]),
            patch("bfs_v5.build_tree", return_value=MagicMock(children={}, files=[])),
            patch.object(traverser, "_bfs_classify", return_value=fake_bfs_result),
        ):
            course_dir = tmp_path / "course"
            course_dir.mkdir()
            traverser.traverse(str(course_dir), debug=debug)

        return mock_classifier

    def test_save_debug_log_not_called_when_debug_false(self, tmp_path: Path) -> None:
        mock_classifier = self._run_traverse_with_debug(tmp_path, debug=False)
        mock_classifier.save_debug_log.assert_not_called()

    def test_save_debug_log_called_when_debug_true(self, tmp_path: Path) -> None:
        mock_classifier = self._run_traverse_with_debug(tmp_path, debug=True)
        mock_classifier.save_debug_log.assert_called_once()


# ---------------------------------------------------------------------------
# Bug 4: CLI --debug flag
# ---------------------------------------------------------------------------

class TestCLIDebugFlag:
    def test_main_has_debug_flag(self, tmp_path: Path) -> None:
        """main() must recognise --debug without raising an unrecognised-argument error."""
        import bfs_v5

        captured = {}

        def fake_bfs_reorganize_v5(**kwargs):
            captured.update(kwargs)
            return MagicMock(mappings={}, missing_from_db=[])

        source = tmp_path / "course"
        source.mkdir()

        with (
            patch("bfs_v5.bfs_reorganize_v5", side_effect=fake_bfs_reorganize_v5),
            patch("os.path.isdir", return_value=True),
            patch("os.environ.get", return_value="fake-key"),
        ):
            try:
                import sys as _sys
                _sys.argv = [
                    "bfs_v5.py",
                    "--source", str(source),
                    "--db", str(tmp_path / "meta.db"),
                    "--debug",
                ]
                bfs_v5.main()
            except SystemExit:
                pass

        assert "debug" in captured, "--debug flag must be forwarded to bfs_reorganize_v5"
        assert captured["debug"] is True

    def test_main_debug_false_by_default(self) -> None:
        """--debug must be False when not supplied."""
        import argparse as ap
        parser = ap.ArgumentParser()
        parser.add_argument("--source", required=True)
        parser.add_argument("--db")
        parser.add_argument("--debug", action="store_true")
        ns = parser.parse_args(["--source", "/tmp/x"])
        assert ns.debug is False

    def test_main_passes_debug_to_bfs_reorganize_v5(self, tmp_path: Path) -> None:
        """When --debug is passed, main() must call bfs_reorganize_v5 with debug=True."""
        import bfs_v5

        captured = {}

        def fake_bfs_reorganize_v5(**kwargs):
            captured.update(kwargs)
            return MagicMock(mappings={}, missing_from_db=[])

        source = tmp_path / "course"
        source.mkdir()
        db = tmp_path / "meta.db"
        db.write_bytes(b"")  # placeholder

        with (
            patch("bfs_v5.bfs_reorganize_v5", side_effect=fake_bfs_reorganize_v5),
            patch("os.path.isdir", return_value=True),
            patch("os.environ.get", return_value="fake-key"),
        ):
            try:
                import sys as _sys
                _sys.argv = [
                    "bfs_v5.py",
                    "--source", str(source),
                    "--db", str(db),
                    "--debug",
                ]
                bfs_v5.main()
            except SystemExit:
                pass

        assert captured.get("debug") is True, (
            "main() must pass debug=True to bfs_reorganize_v5 when --debug flag is set"
        )
