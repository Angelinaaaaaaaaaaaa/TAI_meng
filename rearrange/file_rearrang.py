"""Project-root CLI entry + back-compat re-export shim.

This is the only module in the project that knows about ``argparse``. The
implementation lives under ``src/``; this file:

  1. Adds the parent of this directory to ``sys.path`` so ``import rearrange...``
     works from anywhere (legacy invocations like ``python file_rearrang.py``
     keep functioning from the project root).
  2. Defines the CLI surface (``--step``, ``--input``, ``--db``, ``--course``,
     ``--multi-match``, ``--final-paths``).
  3. Re-exports the public library API for callers that still import from
     ``file_rearrang``.
  4. Dispatches to ``run_pipeline_cli`` when invoked as a script.

New code should import from the fully-qualified paths::

    from rearrange.src.core.steps import collect_orphan_items
    from rearrange.src.services.llm_gateway import LLMGateway
"""

import argparse
import sys
from pathlib import Path
from typing import List, Optional

# Make ``rearrange.*`` importable from anywhere by adding the project's parent.
_PROJECT_ROOT = Path(__file__).resolve().parent
_PARENT = _PROJECT_ROOT.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

# ---- Library re-exports (back-compat for older imports) --------------------
from rearrange.src.core.steps import (  # noqa: F401, E402
    build_tree_from_final_paths,
    collect_orphan_items,
    enrich_structure_with_descriptions,
    extract_backbone_subtree,
    generate_rearrangement_plan,
    reorganize_tree_by_final_paths,
    run_backbone_identification,
)
from rearrange.src.pipeline import (  # noqa: F401, E402
    build_rearranged_structure_tree,
    run_enrichment,
    run_pipeline_cli,
    run_plan_matching,
)
from rearrange.src.services.llm_gateway import (  # noqa: F401, E402
    DEFAULT_LLM_MODEL,
    DEFAULT_LLM_SEED,
    LLMGateway,
)
from rearrange.src.services.models import (  # noqa: F401, E402
    BackboneGroup,
    BackboneResult,
    FileDescription,
    MiscGroupAssignment,
    MiscRefinementResponse,
    OrphanMatch,
    OrphanMatchResponse,
    PipelineContext,
)
from rearrange.src.utils.utils import (  # noqa: F401, E402
    configure_cli_logging,
    load_json_file,
    reset_pipeline_log_dir,
    save_debug_log,
    set_pipeline_log_dir,
)

# These are needed locally for main() but are also exported via the shim above.
from rearrange.src.pipeline import _build_context  # noqa: E402


# ============================================================================
# CLI definition
# ============================================================================

def _parse_bool(value: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in ("true", "t", "yes", "y", "1"):
        return True
    if normalized in ("false", "f", "no", "n", "0"):
        return False
    raise argparse.ArgumentTypeError(
        f"Expected boolean value (true/false), got: {value!r}"
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Course folder rearrangement pipeline")
    parser.add_argument(
        "--step",
        choices=["enrich", "backbone", "match", "tree", "all"],
        default="all",
        help=(
            "Pipeline step. 'enrich' / 'backbone' / 'match' / 'tree' run a single stage; "
            "'all' (default) runs the full pipeline end-to-end."
        ),
    )
    parser.add_argument(
        "--input",
        required=False,
        default=None,
        help=(
            "First-pass tree JSON filename inside 'input/' (or absolute path). "
            "Optional when --final-paths is supplied: passing both reorganizes "
            "the first-pass tree to match team 1's second-pass routing while "
            "preserving any first-pass file metadata (e.g. file_hash). "
            "Required for 'enrich' / 'all' when --final-paths is NOT supplied."
        ),
    )
    parser.add_argument(
        "--db",
        required=False,
        default=None,
        help=(
            "Metadata database filename inside 'input/' (or absolute path). "
            "Auto-detected when exactly one *_metadata.db exists in input/."
        ),
    )
    parser.add_argument(
        "--course",
        required=False,
        default=None,
        help=(
            "Course identifier for output folder. "
            "Auto-derived from --db or --input if not specified."
        ),
    )
    parser.add_argument(
        "--multi-match",
        type=_parse_bool,
        default=True,
        metavar="{true,false}",
        help=(
            "Whether orphans may map to multiple backbone groups. "
            "Pass 'true' (default) for multi-match; 'false' for single-best-group."
        ),
    )
    parser.add_argument(
        "--final-paths",
        required=False,
        default=None,
        help=(
            "Team-1 second-pass classification JSON (filename inside 'input/' or absolute path). "
            "Used together with --input to reorganize the first-pass tree, "
            "or alone to build the tree from scratch. Either way the result is "
            "written to outputs/<course>/v4_tree_reorganized.json for inspection."
        ),
    )
    return parser


def parse_cli_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    return build_arg_parser().parse_args(argv)


def main() -> None:
    """CLI entry point.

    Wired into both ``python file_rearrang.py`` (the legacy invocation) and
    ``python -m rearrange.file_rearrang`` if the package is on ``sys.path``.
    Configures logging, parses args, dispatches to ``run_pipeline_cli``, and
    exits non-zero on failure so shell ``&&`` chains compose correctly.
    """
    configure_cli_logging()
    args = parse_cli_args()
    base_dir = Path.cwd()
    context = _build_context(args, base_dir)
    try:
        run_pipeline_cli(args, base_dir=base_dir, context=context)
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    main()
