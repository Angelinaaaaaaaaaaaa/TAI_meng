"""End-to-end pipeline orchestrator (v2).

Connects the project stages:
  Stage 1   (BFS v5):    bfs_v5.bfs_reorganize_v5()  → final_paths.json + TraversalResult
  Stage 2   (Rearrange): rearrange pipeline           → rearrangement_structure_tree.json
  Stage 2.5 (Map to DB): map_to_db_v3 (tree-json)     → reorganized DB + logical output tree
  Stage 3   (Eval):      evaluation (optional)        → evaluation_report.json

Key design decisions (v2 changes vs v1):
  • RunMode.EVAL_ONLY  — run evaluation without BFS or Rearrange.
  • Zip support        — source_dir and ground_truth_path may be .zip files;
                         they are extracted to a temp dir before use.
  • Debug mode         — intermediate files (plan JSON, tree JSON, report MD,
                         debug/ log dir) are written ONLY when cfg.debug=True.
  • In-memory handoff  — BFS stage returns BfsStageResult containing the live
                         TraversalResult object; Rearrange consumes it directly
                         without re-reading any JSON from disk.
  • bfs_v5             — all BFS calls go through bfs_v5.bfs_reorganize_v5();
                         bfs_v4 is no longer imported.

Usage (CLI):
    python pipeline_orchestrator.py \\
      --source "course_dir" \\
      --db    "course_metadata.db" \\
      [--mode bfs_and_rearrange|rearrange_only|bfs_only|eval_only] \\
      [--eval --ground-truth "gt_dir_or.zip"] \\
      [--debug] \\
      [--multi-match true|false]

Programmatic usage:
    from pipeline_orchestrator import OrchestratorConfig, RunMode, orchestrate

    cfg = OrchestratorConfig(
        source_dir="course_dir",
        db_path="course_metadata.db",
        run_mode=RunMode.BFS_AND_REARRANGE,
        eval_enabled=True,
        ground_truth_path="gt_dir",
        debug=False,
    )
    result = orchestrate(cfg)
    print(result.final_tree_path)
    print(result.missing_from_db)
"""

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("pipeline_orchestrator")


# ---------------------------------------------------------------------------
# Public enums & dataclasses
# ---------------------------------------------------------------------------

class RunMode(str, Enum):
    BFS_AND_REARRANGE = "bfs_and_rearrange"
    REARRANGE_ONLY = "rearrange_only"
    BFS_ONLY = "bfs_only"
    EVAL_ONLY = "eval_only"


@dataclass
class OrchestratorConfig:
    """Validated configuration for the full pipeline."""
    source_dir: str
    db_path: str

    run_mode: RunMode = RunMode.BFS_AND_REARRANGE
    output_dir: Optional[str] = None

    # BFS stage
    bfs_model: str = "gpt-5-mini-2025-08-07"
    bfs_final_paths: Optional[str] = None

    # Rearrange stage
    multi_match: bool = True
    course_name: Optional[str] = None

    # Evaluation stage
    eval_enabled: bool = False
    ground_truth_path: Optional[str] = None
    eval_method: str = "top-down"
    eval_limit: int = 3
    eval_output_dir: Optional[str] = None

    # Map-to-DB stage (Stage 2.5)
    map_enabled: bool = True
    map_output_table: str = "file_new"
    map_logical_root: Optional[str] = None
    map_db_output: Optional[str] = None

    # v2 additions
    debug: bool = False

    def __post_init__(self) -> None:
        if self.run_mode == RunMode.EVAL_ONLY:
            if not self.ground_truth_path:
                raise ValueError(
                    "ground_truth_path is required when run_mode=EVAL_ONLY"
                )
            if not self.bfs_final_paths:
                raise ValueError(
                    "bfs_final_paths (prediction JSON) is required when run_mode=EVAL_ONLY"
                )
            # Eval-only always runs the eval stage.
            self.eval_enabled = True
        else:
            if self.eval_enabled and not self.ground_truth_path:
                raise ValueError(
                    "ground_truth_path is required when eval_enabled=True"
                )
            if self.run_mode == RunMode.REARRANGE_ONLY and not self.bfs_final_paths:
                raise ValueError(
                    "bfs_final_paths is required when run_mode=REARRANGE_ONLY"
                )

        if self.output_dir is None:
            self.output_dir = str(Path(self.source_dir).parent / "outputs")


@dataclass
class BfsStageResult:
    """Returned by run_bfs_stage; carries both the in-memory result and persisted paths."""
    traversal_result: Any                    # bfs_v5.TraversalResult
    final_paths_json_path: str               # always written
    tree_json_path: Optional[str] = None     # written only in debug mode
    missing_from_db: List[str] = field(default_factory=list)


@dataclass
class MapStageResult:
    """Returned by run_map_stage; describes the materialized DB + logical tree."""
    output_db_path: str
    output_logical_root: str
    output_table: str


@dataclass
class PipelineResult:
    """Returned by orchestrate(); never raises."""
    success: bool
    final_tree_path: Optional[str] = None
    bfs_paths: Optional[Dict[str, str]] = None
    bfs_stage_result: Optional[BfsStageResult] = None
    map_stage_result: Optional[MapStageResult] = None
    eval_report: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    @property
    def missing_from_db(self) -> List[str]:
        if self.bfs_stage_result:
            return self.bfs_stage_result.missing_from_db
        return []


# ---------------------------------------------------------------------------
# Zip / path resolution
# ---------------------------------------------------------------------------

def resolve_path_or_zip(path: str) -> Tuple[str, Optional[Callable[[], None]]]:
    """Return (resolved_path, cleanup_fn).

    If *path* is a directory, returns it unchanged with ``cleanup_fn=None``.
    If *path* is a .zip file, extracts it to a temp directory and returns
    that directory path with a cleanup function that deletes it.
    """
    p = Path(path)
    if p.is_dir():
        return str(p), None

    if p.suffix.lower() == ".zip" and p.is_file():
        tmp = tempfile.mkdtemp(prefix="orch_zip_")
        with zipfile.ZipFile(str(p)) as zf:
            zf.extractall(tmp)

        # If the zip contains a single top-level directory, use that.
        entries = list(Path(tmp).iterdir())
        if len(entries) == 1 and entries[0].is_dir():
            extracted = str(entries[0])
        else:
            extracted = tmp

        def _cleanup():
            shutil.rmtree(tmp, ignore_errors=True)

        return extracted, _cleanup

    return str(p), None


@contextmanager
def _resolved_path(path: str):
    """Context manager that resolves a path-or-zip and cleans up on exit."""
    resolved, cleanup = resolve_path_or_zip(path)
    try:
        yield resolved
    finally:
        if cleanup:
            cleanup()


# ---------------------------------------------------------------------------
# Lazy imports — kept as module-level callables for easy patching in tests.
# ---------------------------------------------------------------------------

def bfs_reorganize_v5(*args, **kwargs):
    from bfs_v5 import bfs_reorganize_v5 as _fn
    return _fn(*args, **kwargs)


def run_pipeline_cli(*args, **kwargs):
    from rearrange.src.pipeline import run_pipeline_cli as _fn
    return _fn(*args, **kwargs)


def _map_to_db_module():
    import map_to_db_v3
    return map_to_db_v3


_COURSE_ALIASES = {
    "cs61a": "cs61a", "61a": "cs61a", "cs_61a": "cs61a",
    "eecs106b": "eecs106b", "106b": "eecs106b", "eecs_106b": "eecs106b",
}


def _resolve_map_course(raw: Optional[str], db_path: str) -> str:
    """Normalize a user-supplied course string to map_to_db_v3's accepted form."""
    candidates = []
    if raw:
        candidates.append(raw)
    candidates.append(Path(db_path).stem)

    for cand in candidates:
        key = cand.lower().replace("-", "_").replace(" ", "_")
        for alias, canonical in _COURSE_ALIASES.items():
            if alias in key:
                return canonical

    raise ValueError(
        f"Cannot determine map-to-db course from {raw!r}/{db_path!r}. "
        "Pass --map-course cs61a or eecs106b."
    )


def run_evaluation(*args, **kwargs):
    """Thin bridge into evaluation/evaluate.py."""
    from evaluation.evaluate import evaluate_tree
    from evaluation.build_data import (
        get_ground_truth_hashes,
        construct_tree_from_json,
        create_folder_children_dict,
    )
    from evaluation.utils import normalize_db_path_eval
    from contextlib import chdir
    return _run_evaluation_impl(
        evaluate_tree=evaluate_tree,
        get_ground_truth_hashes=get_ground_truth_hashes,
        construct_tree_from_json=construct_tree_from_json,
        create_folder_children_dict=create_folder_children_dict,
        normalize_db_path_eval=normalize_db_path_eval,
        chdir=chdir,
        *args,
        **kwargs,
    )


def _run_evaluation_impl(
    *,
    evaluate_tree,
    get_ground_truth_hashes,
    construct_tree_from_json,
    create_folder_children_dict,
    normalize_db_path_eval,
    chdir,
    db_path: str,
    ground_truth_path: str,
    prediction_path: str,
    method: str = "top-down",
    limit: int = 3,
) -> Dict[str, Any]:
    unnormalized_hashes = get_ground_truth_hashes(db_path)
    prediction_tree = construct_tree_from_json(unnormalized_hashes, prediction_path)

    ground_truth_hashes = {
        normalize_db_path_eval(path): file_hash
        for path, file_hash in unnormalized_hashes.items()
    }

    ground_truth_tree = {}
    with chdir(ground_truth_path):
        create_folder_children_dict(Path("."), ground_truth_tree, ground_truth_hashes)

    return evaluate_tree(ground_truth_tree, prediction_tree, method=method, limit=limit)


# ---------------------------------------------------------------------------
# Stage 1: BFS
# ---------------------------------------------------------------------------

def run_bfs_stage(cfg: OrchestratorConfig) -> BfsStageResult:
    """Run BFS v5 classification.

    Returns a BfsStageResult containing the live TraversalResult in memory
    plus the path to the written final_paths JSON.
    Intermediate files (plan JSON, tree JSON, report MD) are only written
    when cfg.debug=True.
    """
    output_dir = Path(cfg.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if cfg.debug:
        final_paths_out = str(output_dir / "bfs_v5_final_paths.json")
    else:
        # Non-debug: write to a tempfile that the orchestrator cleans up after
        # the run. Avoids stranded JSON in outputs/ that needs periodic cleanup.
        tmp_fd, tmp_path = tempfile.mkstemp(prefix="bfs_v5_final_paths_", suffix=".json")
        os.close(tmp_fd)
        final_paths_out = tmp_path
    tree_out = str(output_dir / "bfs_v5_tree.json") if cfg.debug else None
    report_out = str(output_dir / "bfs_v5_report.md") if cfg.debug else None
    json_out = str(output_dir / "bfs_v5_plan.json") if cfg.debug else None

    log.info("=" * 60)
    log.info("Stage 1: BFS v5 classification  (%s)", cfg.source_dir)
    log.info("=" * 60)

    with _resolved_path(cfg.source_dir) as resolved_source:
        traversal_result = bfs_reorganize_v5(
            course_root=resolved_source,
            db_path=cfg.db_path,
            model=cfg.bfs_model,
            final_paths_path=final_paths_out,
            tree_path=tree_out,
            report_path=report_out,
            json_path=json_out,
            debug=cfg.debug,
        )

    missing = getattr(traversal_result, "missing_from_db", [])
    log.info("BFS complete. final_paths → %s", final_paths_out)
    if missing:
        log.warning("  %d file(s) on disk have no DB entry: %s ...", len(missing), missing[:3])

    return BfsStageResult(
        traversal_result=traversal_result,
        final_paths_json_path=final_paths_out,
        tree_json_path=tree_out,
        missing_from_db=list(missing),
    )


# ---------------------------------------------------------------------------
# Stage 2: Rearrange
# ---------------------------------------------------------------------------

def run_rearrange_stage(
    cfg: OrchestratorConfig,
    *,
    final_paths_path: Optional[str] = None,
    bfs_tree_path: Optional[str] = None,
    bfs_stage_result: Optional[BfsStageResult] = None,
) -> str:
    """Run the rearrangement pipeline (Part 2).

    Accepts either:
      • bfs_stage_result — in-memory BfsStageResult from run_bfs_stage(); the
        final_paths JSON path is read from it (no file re-read needed).
      • final_paths_path — explicit path string (legacy / REARRANGE_ONLY mode).

    Returns the path to ``rearrangement_structure_tree.json``.

    Intermediate files (debug/) are propagated via the ``args.debug`` flag
    passed to run_pipeline_cli when cfg.debug=True.
    """
    output_dir = Path(cfg.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Resolve final_paths_path from the stage result when available (in-memory handoff).
    if bfs_stage_result is not None:
        fp = bfs_stage_result.final_paths_json_path
        tree_p = bfs_stage_result.tree_json_path
    else:
        fp = final_paths_path
        tree_p = bfs_tree_path

    course_name = cfg.course_name
    if not course_name:
        db_stem = Path(cfg.db_path).stem
        course_name = db_stem.replace("_metadata", "").replace("_reorganization", "")

    log.info("=" * 60)
    log.info("Stage 2: Rearrangement pipeline  (course=%s)", course_name)
    log.info("=" * 60)

    args = argparse.Namespace(
        step="all",
        input=tree_p,
        db=cfg.db_path,
        course=course_name,
        multi_match=cfg.multi_match,
        final_paths=fp,
        debug=cfg.debug,
    )

    run_pipeline_cli(args, base_dir=output_dir)

    # The rearrange pipeline writes under outputs/<course>/ or directly.
    tree_path = output_dir / course_name / "rearrangement_structure_tree.json"
    if not tree_path.exists():
        tree_path = output_dir / "rearrangement_structure_tree.json"

    if not tree_path.exists():
        raise FileNotFoundError(
            f"Rearrangement tree not found at expected path(s) under {output_dir}. "
            "Check that the rearrange pipeline completed successfully."
        )

    log.info("Rearrange complete. tree → %s", tree_path)
    return str(tree_path)


# ---------------------------------------------------------------------------
# Stage 2.5: Map to DB
# ---------------------------------------------------------------------------

def run_map_stage(
    cfg: OrchestratorConfig,
    *,
    tree_json_path: str,
) -> MapStageResult:
    """Materialize the rearrangement tree into a reorganized DB + logical folder.

    Consumes ``rearrangement_structure_tree.json`` (file nodes with
    ``source``/``final_path``) via map_to_db_v3's tree-json mode.

    Outputs are real deliverables (not intermediates), so they are written
    regardless of cfg.debug.
    """
    output_dir = Path(cfg.output_dir)
    course = _resolve_map_course(cfg.course_name, cfg.db_path)

    map_root = Path(cfg.map_logical_root) if cfg.map_logical_root \
        else output_dir / course / "logical"
    map_db = Path(cfg.map_db_output) if cfg.map_db_output \
        else output_dir / course / f"{course}_reorganized.db"

    map_db.parent.mkdir(parents=True, exist_ok=True)
    map_root.parent.mkdir(parents=True, exist_ok=True)

    log.info("=" * 60)
    log.info("Stage 2.5: Map to DB  (course=%s, table=%s)", course, cfg.map_output_table)
    log.info("=" * 60)

    mod = _map_to_db_module()
    runner = mod.build_runner(
        course=course,
        output_table=cfg.map_output_table,
        create_logical_output=False,
        create_folder_symlinks=True,
    )
    # The course module's hardcoded DB_PATH is its own per-course default; point
    # it at this run's metadata DB so map_to_db copies the right base.
    runner.module.DB_PATH = str(Path(cfg.db_path).resolve())

    with _resolved_path(cfg.source_dir) as resolved_source:
        mod.run_tree_json_mode(
            module=runner.module,
            tree_json=tree_json_path,
            source_root=resolved_source,
            output_root=str(map_root),
            output_db=str(map_db),
            output_table=cfg.map_output_table,
        )

    log.info("Map-to-DB complete. db → %s | tree → %s", map_db, map_root)
    return MapStageResult(
        output_db_path=str(map_db),
        output_logical_root=str(map_root),
        output_table=cfg.map_output_table,
    )


# ---------------------------------------------------------------------------
# Stage 3: Evaluation
# ---------------------------------------------------------------------------

def run_evaluation_stage(
    cfg: OrchestratorConfig,
    *,
    prediction_path: str,
) -> Optional[Dict[str, Any]]:
    """Run evaluation against ground truth (Part 3, optional).

    Returns the report dict or ``None`` when eval is disabled.
    Ground-truth may be a directory or a .zip file.
    """
    if not cfg.eval_enabled:
        return None

    log.info("=" * 60)
    log.info("Stage 3: Evaluation  (method=%s, limit=%d)", cfg.eval_method, cfg.eval_limit)
    log.info("=" * 60)

    with _resolved_path(cfg.ground_truth_path) as resolved_gt:
        report = run_evaluation(
            db_path=cfg.db_path,
            ground_truth_path=resolved_gt,
            prediction_path=prediction_path,
            method=cfg.eval_method,
            limit=cfg.eval_limit,
        )

    if cfg.debug:
        eval_out_dir = Path(cfg.eval_output_dir or Path(cfg.output_dir) / "eval_reports")
        eval_out_dir.mkdir(parents=True, exist_ok=True)

        pred_stem = Path(prediction_path).stem
        gt_stem = Path(cfg.ground_truth_path).name.replace(".zip", "")
        report_file = eval_out_dir / f"{gt_stem}_{pred_stem}_{cfg.eval_method}_limit_{cfg.eval_limit}.json"
        report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
        log.info("Evaluation complete. report → %s", report_file)
    else:
        log.info("Evaluation complete. (report kept in memory; pass --debug to persist)")
    return report


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------

def orchestrate(cfg: OrchestratorConfig) -> PipelineResult:
    """Run all enabled pipeline stages in order.  Never raises."""
    bfs_result: Optional[BfsStageResult] = None
    final_tree_path: Optional[str] = None
    map_result: Optional[MapStageResult] = None
    eval_report: Optional[Dict[str, Any]] = None

    try:
        # ---- Stage 1: BFS ------------------------------------------------
        if cfg.run_mode in (RunMode.BFS_AND_REARRANGE, RunMode.BFS_ONLY):
            log.info("Starting BFS stage")
            bfs_result = run_bfs_stage(cfg)

        # ---- Stage 2: Rearrange ------------------------------------------
        if cfg.run_mode in (RunMode.BFS_AND_REARRANGE, RunMode.REARRANGE_ONLY):
            log.info("Starting rearrange stage")
            final_tree_path = run_rearrange_stage(
                cfg,
                final_paths_path=cfg.bfs_final_paths if bfs_result is None else None,
                bfs_stage_result=bfs_result,
            )

        # ---- Stage 2.5: Map to DB ----------------------------------------
        if cfg.map_enabled and final_tree_path:
            log.info("Starting map-to-db stage")
            map_result = run_map_stage(cfg, tree_json_path=final_tree_path)

        # ---- Stage 3: Evaluation -----------------------------------------
        if cfg.run_mode == RunMode.EVAL_ONLY:
            eval_prediction = cfg.bfs_final_paths
        elif bfs_result:
            eval_prediction = bfs_result.final_paths_json_path
        else:
            eval_prediction = cfg.bfs_final_paths

        if eval_prediction:
            eval_report = run_evaluation_stage(cfg, prediction_path=eval_prediction)

    except Exception as exc:
        log.exception("Pipeline failed: %s", exc)
        return PipelineResult(success=False, error=str(exc))
    finally:
        # When debug=False we wrote BFS final_paths to a tempfile (see
        # run_bfs_stage). Clean it up now that downstream stages are done.
        if (
            not cfg.debug
            and bfs_result is not None
            and bfs_result.final_paths_json_path
        ):
            try:
                Path(bfs_result.final_paths_json_path).unlink(missing_ok=True)
            except OSError as e:
                log.warning("Could not remove temp final_paths file: %s", e)

    # Build backward-compat bfs_paths dict for callers that used the old API.
    bfs_paths: Optional[Dict[str, str]] = None
    if bfs_result:
        bfs_paths = {"final_paths": bfs_result.final_paths_json_path}
        if bfs_result.tree_json_path:
            bfs_paths["tree"] = bfs_result.tree_json_path

    return PipelineResult(
        success=True,
        final_tree_path=final_tree_path,
        bfs_paths=bfs_paths,
        bfs_stage_result=bfs_result,
        map_stage_result=map_result,
        eval_report=eval_report,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_cli_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="End-to-end course reorganization pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Full pipeline (BFS v5 + rearrange):\n"
            "  python pipeline_orchestrator.py \\\n"
            "    --source 'EECS_106B/' --db EECS_106B_metadata.db\n\n"
            "  # Rearrange only (existing BFS output):\n"
            "  python pipeline_orchestrator.py \\\n"
            "    --mode rearrange_only \\\n"
            "    --source 'EECS_106B/' --db EECS_106B_metadata.db \\\n"
            "    --bfs-final-paths outputs/bfs_v5_final_paths.json\n\n"
            "  # Evaluation only:\n"
            "  python pipeline_orchestrator.py \\\n"
            "    --mode eval_only \\\n"
            "    --source 'CS_61A/' --db CS_61A_metadata.db \\\n"
            "    --bfs-final-paths outputs/bfs_v5_final_paths.json \\\n"
            "    --eval --ground-truth gt/61A_gt.zip\n\n"
            "  # Full pipeline + evaluation + debug files:\n"
            "  python pipeline_orchestrator.py \\\n"
            "    --source 'CS_61A/' --db CS_61A_metadata.db \\\n"
            "    --eval --ground-truth gt/61A_gt/ --debug\n"
        ),
    )
    p.add_argument("--source", "-s", required=True, help="Course root directory or .zip")
    p.add_argument("--db", "-d", required=True, help="SQLite metadata database path")
    p.add_argument(
        "--mode",
        choices=[m.value for m in RunMode],
        default=RunMode.BFS_AND_REARRANGE.value,
        help="Which stages to run (default: bfs_and_rearrange)",
    )
    p.add_argument("--output-dir", default=None, help="Root output directory")
    p.add_argument("--course", default=None, help="Course identifier (auto-derived if omitted)")
    p.add_argument("--bfs-model", default="gpt-5-mini-2025-08-07", help="OpenAI model for BFS")
    p.add_argument(
        "--bfs-final-paths",
        default=None,
        help="Pre-computed BFS final-paths JSON (required for --mode rearrange_only/eval_only)",
    )
    p.add_argument(
        "--multi-match",
        type=lambda v: v.lower() not in ("false", "0", "no"),
        default=True,
        metavar="{true,false}",
        help="Allow orphan files to match multiple backbone groups (default: true)",
    )
    p.add_argument(
        "--map",
        dest="map_enabled",
        type=lambda v: v.lower() not in ("false", "0", "no"),
        default=True,
        metavar="{true,false}",
        help="Run Stage 2.5 map-to-db (default: true)",
    )
    p.add_argument("--map-output-table", default="file_new", help="Map-to-db output table name")
    p.add_argument("--map-logical-root", default=None, help="Map-to-db logical output folder")
    p.add_argument("--map-db-output", default=None, help="Map-to-db output metadata DB path")
    p.add_argument("--eval", action="store_true", dest="eval_enabled", help="Run evaluation stage")
    p.add_argument(
        "--ground-truth",
        default=None,
        help="Ground-truth directory or .zip (required with --eval / --mode eval_only)",
    )
    p.add_argument(
        "--eval-method",
        choices=["top-down", "bottom-up"],
        default="top-down",
        help="Evaluation method",
    )
    p.add_argument("--eval-limit", type=int, default=3, help="Path depth limit for evaluation")
    p.add_argument("--eval-output-dir", default=None, help="Directory for evaluation reports")
    p.add_argument(
        "--debug",
        action="store_true",
        help=(
            "Write intermediate files: BFS plan JSON, tree JSON, report MD, "
            "and rearrange debug/ logs. Off by default."
        ),
    )
    p.add_argument("--verbose", "-v", action="store_true")
    return p


def main(argv=None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        cfg = OrchestratorConfig(
            source_dir=args.source,
            db_path=args.db,
            run_mode=RunMode(args.mode),
            output_dir=args.output_dir,
            course_name=args.course,
            bfs_model=args.bfs_model,
            bfs_final_paths=args.bfs_final_paths,
            multi_match=args.multi_match,
            eval_enabled=args.eval_enabled,
            ground_truth_path=args.ground_truth,
            eval_method=args.eval_method,
            eval_limit=args.eval_limit,
            eval_output_dir=args.eval_output_dir,
            map_enabled=args.map_enabled,
            map_output_table=args.map_output_table,
            map_logical_root=args.map_logical_root,
            map_db_output=args.map_db_output,
            debug=args.debug,
        )
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    result = orchestrate(cfg)

    if result.success:
        print("\n" + "=" * 60)
        print("Pipeline completed successfully.")
        if result.bfs_paths:
            print(f"  BFS final-paths : {result.bfs_paths['final_paths']}")
            if result.bfs_paths.get("tree"):
                print(f"  BFS tree        : {result.bfs_paths['tree']}")
        if result.missing_from_db:
            print(f"  Missing from DB : {len(result.missing_from_db)} file(s)")
        if result.final_tree_path:
            print(f"  Structure tree  : {result.final_tree_path}")
        if result.map_stage_result:
            print(f"  Reorganized DB  : {result.map_stage_result.output_db_path}")
            print(f"  Logical output  : {result.map_stage_result.output_logical_root}")
        if result.eval_report:
            f1 = result.eval_report.get("f1")
            if f1 is not None:
                print(f"  Evaluation F1   : {f1:.4f}")
            else:
                print("  Evaluation report saved.")
        print("=" * 60)
    else:
        print(f"\nPipeline FAILED: {result.error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
