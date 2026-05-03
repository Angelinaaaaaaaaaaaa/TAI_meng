"""Top-level orchestration: enrichment → backbone → match → tree, plus CLI entry.

Sections:
  1. Context + path resolution        (uses pathlib for cross-platform safety)
  2. Pipeline steps (enrich, match)
  3. Tree builder (materialize plan to hierarchical JSON with file hashes)
  4. CLI orchestration + entry point

Layout per run:
    outputs/<course>[/multi]/
        study_enriched.json                  (enrich)
        backbone_result.json                 (backbone)
        orphan_matches.json                  (match)
        rearrangement_plan.json              (match)
        rearrangement_structure_tree.json    (tree — final artifact)
        debug/                               (numbered checkpoints, observability only)
"""

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from dotenv import load_dotenv

from models import OrphanMatchResponse, PipelineContext
from steps import (
    _append_unmatched_orphans_to_misc,
    _build_matching_system_prompt,
    _filter_matches,
    _make_backbone_groups,
    build_summary,
    collect_orphan_items,
    enrich_structure_with_descriptions,
    extract_backbone_subtree,
    generate_rearrangement_plan,
    run_backbone_identification,
)
from utils import (
    LLMGateway,
    _chunked,
    _derive_course_name,
    _detect_course_prefix,
    _safe_print,
    load_json_file,
    reset_pipeline_log_dir,
    save_debug_log,
    set_pipeline_log_dir,
)

load_dotenv()

PathLike = Union[str, Path]


# =============================================================================
# 1. Context + path resolution
# =============================================================================

def _build_context(args, base_dir: PathLike) -> PipelineContext:
    base = Path(base_dir)
    course_name = args.course or _derive_course_name(args.db, args.input)
    if bool(getattr(args, "multi_match", False)):
        course_name = str(Path(course_name) / "multi")

    output_dir = base / "outputs" / course_name
    log_dir = output_dir / "debug"  # unified: logs live under outputs/<course>/debug

    return PipelineContext(
        base_dir=str(base),
        course_name=course_name,
        output_dir=str(output_dir),
        log_dir=str(log_dir),
        multi_match=bool(getattr(args, "multi_match", False)),
    )


def _resolve_db_path(base_dir: PathLike, db_arg: Optional[str] = None) -> Path:
    """Resolve the metadata database path from --db arg or auto-detect from input/."""
    input_dir = Path(base_dir) / "input"
    if db_arg:
        candidate = input_dir / db_arg
        return candidate if candidate.exists() else Path(db_arg)

    db_candidates = sorted(input_dir.glob("*_metadata.db"))
    if len(db_candidates) == 1:
        return db_candidates[0]
    if len(db_candidates) > 1:
        names = [p.name for p in db_candidates]
        raise FileNotFoundError(
            f"Multiple metadata databases found in input/: {names}. Use --db to specify which one."
        )
    raise FileNotFoundError(
        "No *_metadata.db file found in input/ folder. Use --db to specify one."
    )


def _resolve_input_path(base_dir: PathLike, input_arg: str) -> Path:
    """--input may be a filename inside input/ or an absolute/relative path."""
    candidate = Path(base_dir) / "input" / input_arg
    return candidate if candidate.exists() else Path(input_arg)


def _load_enriched(base_dir: PathLike, course_name: Optional[str] = None) -> Dict:
    base = Path(base_dir)
    enriched_file = (
        base / "outputs" / course_name / "study_enriched.json"
        if course_name
        else base / "outputs" / "study_enriched.json"
    )
    if not enriched_file.exists():
        raise FileNotFoundError(
            f"Enriched tree not found at {enriched_file}. Run the 'enrich' step first."
        )
    return load_json_file(str(enriched_file))


def _write_json(path: Path, data, *, indent: int = 4) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)


# =============================================================================
# 2. Pipeline steps
# =============================================================================

def run_enrichment(
    base_dir: PathLike,
    input_filename: str,
    db_filename: Optional[str] = None,
    course_name: Optional[str] = None,
    multi_match: bool = False,
) -> str:
    """Preprocessing: parse input tree JSON + SQLite DB → enriched JSON."""
    base = Path(base_dir)
    input_file = _resolve_input_path(base, input_filename)

    if db_filename:
        db_file = _resolve_input_path(base, db_filename)
    else:
        db_file = _resolve_db_path(base, None)

    if not course_name:
        course_name = _derive_course_name(db_filename, input_filename)

    output_dir = base / "outputs" / course_name
    output_dir.mkdir(parents=True, exist_ok=True)
    enriched_output = output_dir / "study_enriched.json"

    print("=" * 60)
    print(f"Preprocessing: Enriching structure for course '{course_name}'")
    print("=" * 60)
    print(f"Database path: {db_file}")
    print(f"Input file path: {input_file}")

    return enrich_structure_with_descriptions(
        str(input_file), str(db_file), str(enriched_output), multi_match=multi_match
    )


def run_plan_matching(
    enriched_data: Dict,
    backbone_path: str,
    multi_match: bool = False,
    *,
    llm_gateway: Optional[LLMGateway] = None,
) -> Tuple[OrphanMatchResponse, List[Dict]]:
    """Match non-backbone items to backbone groups and produce the rearrangement plan."""
    gateway = llm_gateway or LLMGateway()

    # --- Step A: Generate backbone groups ---
    backbone_subtree = extract_backbone_subtree(enriched_data, backbone_path)
    if not backbone_subtree:
        raise ValueError(f"Backbone folder '{backbone_path}' not found in enriched data.")

    save_debug_log(backbone_subtree, "02_1_backbone_subtree")
    backbone_groups = _make_backbone_groups(backbone_subtree)

    print(f"Generated {len(backbone_groups)} backbone groups.")
    save_debug_log([g.model_dump() for g in backbone_groups], "02_2_backbone_groups")

    # --- Step B: Collect orphans and match ---
    orphans = collect_orphan_items(enriched_data, backbone_path, [])
    print(f"Identified {len(orphans)} orphan items needing placement.")
    save_debug_log(orphans, "02_3_orphans_collected")

    if not orphans:
        print("No orphans detected.")
        empty = OrphanMatchResponse(matches=[])
        return empty, generate_rearrangement_plan(backbone_groups, empty, llm_gateway=gateway)

    print("Sample orphans:")
    for o in orphans[:5]:
        _safe_print(f" - {o['name']} ({o['type']})")

    groups_summary = build_summary(backbone_groups)

    all_matches = []
    chunk_size = 50
    total_orphans = len(orphans)
    print(f"Processing {total_orphans} orphans in batches of {chunk_size}...")

    system_prompt = _build_matching_system_prompt(backbone_path, multi_match)

    for batch_index, batch_orphans in enumerate(_chunked(orphans, chunk_size), start=1):
        print(
            f"Processing batch {batch_index} / "
            f"{(total_orphans + chunk_size - 1) // chunk_size}..."
        )

        try:
            batch_result = gateway.parse_structured(
                model="gpt-5-mini",
                system_prompt=system_prompt,
                user_payload={
                    "backbone_folder": backbone_path,
                    "existing_groups": groups_summary,
                    "orphans": batch_orphans,
                },
                response_model=OrphanMatchResponse,
                seed=42,
            )

            filtered_matches = _filter_matches(batch_result, batch_orphans)
            if filtered_matches:
                all_matches.extend(filtered_matches)
                print(f"  - Matched {len(filtered_matches)} valid items in this batch.")
            else:
                print("  - Warning: No matches returned for this batch.")

        except Exception as e:
            _safe_print(f"  - Error processing batch: {e}")

    matches = OrphanMatchResponse(matches=all_matches)
    unmatched_added = _append_unmatched_orphans_to_misc(orphans, matches.matches)
    if unmatched_added:
        print(f"Added {unmatched_added} unmatched orphan items to 'Lecture Miscellaneous'.")
    print(f"Matched total of {len(matches.matches)} orphan items to groups.")

    plan = generate_rearrangement_plan(backbone_groups, matches, llm_gateway=gateway)
    return matches, plan


def run_file_rearrangement(orphan_matches: OrphanMatchResponse, enriched_data: Dict):
    """Placeholder for file rearrangement — not implemented yet."""
    raise NotImplementedError("File rearrangement is not yet implemented.")


# =============================================================================
# 3. Tree builder
# =============================================================================

def load_file_hashes(db_path: PathLike) -> Dict[str, str]:
    """Load all file hashes from the database into a dict keyed by relative_path."""
    db = Path(db_path)
    if not db.exists():
        print(f"Warning: Database not found at {db}. Hashes will be empty.")
        return {}

    conn = sqlite3.connect(str(db))
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT relative_path, file_hash, file_name FROM file")
        rows = cursor.fetchall()
        result: Dict[str, str] = {}

        course_prefix = _detect_course_prefix([r[0] for r in rows if r[0]])

        for path, h, filename in rows:
            if not path:
                continue
            result[path] = h
            if course_prefix and path.startswith(course_prefix):
                result[path[len(course_prefix):]] = h
            if filename:
                result[f"__NAME__{filename}"] = h

        print(f"Loaded {len(rows)} file rows from database. Lookup map size: {len(result)}")
        if course_prefix:
            print(f"Detected course prefix: '{course_prefix}'")
        return result
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return {}
    finally:
        conn.close()


def index_enriched_data(enriched_data: Dict) -> Dict[str, Dict]:
    """Index the enriched data tree by relative_path AND name for fast lookups."""
    index: Dict[str, Dict] = {}

    def traverse(node):
        path = node.get("relative_path")
        name = node.get("name")
        if path:
            index[path] = node
        if name:
            index[f"__NAME__{name}"] = node
        for child in node.get("children", []) or []:
            traverse(child)

    for child in enriched_data.get("children", []) or []:
        traverse(child)

    return index


def build_node_recursive(original_node: Dict, hash_map: Dict[str, str]) -> Dict:
    """Recursively build a node for the new tree, populating hashes for files."""
    new_node = {
        "type": original_node.get("type", "folder"),
        "name": original_node.get("name"),
        "relative_path": original_node.get("relative_path"),
        "description": original_node.get("description", ""),
    }

    if new_node["type"] == "file":
        rel_path = new_node.get("relative_path", "")
        file_hash = hash_map.get(rel_path)
        if not file_hash:
            fname = new_node.get("name")
            if fname:
                file_hash = hash_map.get(f"__NAME__{fname}")
        new_node["file_hash"] = file_hash or ""
    elif new_node["type"] == "folder":
        new_node["children"] = [
            build_node_recursive(c, hash_map) for c in (original_node.get("children") or [])
        ]

    return new_node


def _load_plan_groups(plan_data: object) -> List[Dict]:
    if isinstance(plan_data, dict) and "groups" in plan_data:
        return plan_data["groups"]
    if isinstance(plan_data, list):
        return plan_data
    return []


def _dedupe_items(items: List[str]) -> List[str]:
    seen = set()
    unique_items = []
    for item in items:
        if item and item not in seen:
            unique_items.append(item)
            seen.add(item)
    return unique_items


def _resolve_original_node(item_path: str, enriched_index: Dict[str, Dict]) -> Optional[Dict]:
    original_node = enriched_index.get(item_path)
    if original_node:
        return original_node
    return enriched_index.get(f"__NAME__{Path(item_path).name}")


def _build_group_node(
    group: Dict, enriched_index: Dict[str, Dict], hash_map: Dict[str, str]
) -> Dict:
    group_name = group.get("group_name", "Unnamed Group")
    main_item = group.get("main_item")
    related_items = group.get("related_items", [])

    group_node = {"type": "group", "name": group_name, "children": []}

    items_to_process = []
    if main_item:
        items_to_process.append(main_item)
    if related_items:
        items_to_process.extend(related_items)

    for item_path in _dedupe_items(items_to_process):
        original_node = _resolve_original_node(item_path, enriched_index)

        if original_node:
            group_node["children"].append(build_node_recursive(original_node, hash_map))
            continue

        try:
            print(f"Warning: Item not found in enriched data: {item_path}")
        except UnicodeEncodeError:
            print(
                "Warning: Item not found in enriched data: "
                + item_path.encode("ascii", "replace").decode("ascii")
            )

        basename = Path(item_path).name
        fallback_hash = hash_map.get(item_path) or hash_map.get(f"__NAME__{basename}", "")
        group_node["children"].append(
            {
                "type": "unknown",
                "relative_path": item_path,
                "name": basename,
                "file_hash": fallback_hash,
            }
        )

    return group_node


def build_rearranged_structure_tree(
    plan_path: PathLike, enriched_data_path: PathLike, db_path: PathLike, output_path: PathLike
) -> None:
    """Build a full hierarchical tree based on the rearrangement plan."""
    print("Building rearranged structure tree...")

    try:
        plan_data = load_json_file(str(plan_path))
        enriched_data = load_json_file(str(enriched_data_path))
        hash_map = load_file_hashes(db_path)
    except Exception as e:
        print(f"Error loading inputs: {e}")
        return

    enriched_index = index_enriched_data(enriched_data)

    plan_groups = _load_plan_groups(plan_data)
    if not plan_groups:
        print("Error: Invalid plan format")
        return

    result_tree = [_build_group_node(g, enriched_index, hash_map) for g in plan_groups]
    _write_json(Path(output_path), result_tree, indent=2)
    print(f"Rearranged structure tree saved to: {output_path}")


# =============================================================================
# 4. CLI
# =============================================================================

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
            "Tree JSON filename inside 'input/' (or absolute path). "
            "Required for the 'enrich' and 'all' steps; ignored otherwise."
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
    return parser


def parse_cli_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    return build_arg_parser().parse_args(argv)


def _step_enrich(context: PipelineContext, base_dir: Path, args: argparse.Namespace) -> None:
    if not args.input:
        raise ValueError(
            "--input is required for the 'enrich' / 'all' steps. "
            "Pass the tree JSON filename (relative to input/) or an absolute path."
        )
    run_enrichment(
        base_dir,
        args.input,
        args.db,
        context.course_name,
        multi_match=context.multi_match,
    )


def _step_backbone(context: PipelineContext, base_dir: Path, args: argparse.Namespace) -> None:
    print("=" * 60)
    print("Step 1: Backbone Identification")
    print("=" * 60)
    enriched_data = _load_enriched(base_dir, context.course_name)
    backbone_path = run_backbone_identification(enriched_data)

    backbone_output = Path(context.output_dir) / "backbone_result.json"
    _write_json(backbone_output, {"backbone_path": backbone_path})
    print(f"Backbone result saved to: {backbone_output}")


def _step_match(context: PipelineContext, base_dir: Path, args: argparse.Namespace) -> None:
    print("=" * 60)
    print("Step 2: Orphan Matching")
    print("=" * 60)
    enriched_data = _load_enriched(base_dir, context.course_name)

    backbone_file = Path(context.output_dir) / "backbone_result.json"
    if backbone_file.exists():
        backbone_path = load_json_file(str(backbone_file))["backbone_path"]
    else:
        print("No backbone result found, running backbone identification first...")
        backbone_path = run_backbone_identification(enriched_data)

    matches, plan = run_plan_matching(
        enriched_data,
        backbone_path,
        multi_match=context.multi_match,
    )

    matches_output = Path(context.output_dir) / "orphan_matches.json"
    _write_json(matches_output, matches.model_dump())
    print(f"Orphan matches saved to: {matches_output}")

    plan_output = Path(context.output_dir) / "rearrangement_plan.json"
    _write_json(plan_output, plan)
    print(f"Rearrangement plan saved to: {plan_output}")


def _step_tree(context: PipelineContext, base_dir: Path, args: argparse.Namespace) -> None:
    print("=" * 60)
    print("Step 3: Tree Materialization")
    print("=" * 60)
    output_dir = Path(context.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    plan_path = output_dir / "rearrangement_plan.json"
    if not plan_path.exists():
        raise FileNotFoundError(
            f"Rearrangement plan not found at {plan_path}. Run the 'match' step first."
        )
    enriched_path = Path(base_dir) / "outputs" / context.course_name / "study_enriched.json"
    if not enriched_path.exists():
        raise FileNotFoundError(
            f"Enriched tree not found at {enriched_path}. Run the 'enrich' step first."
        )

    db_path = _resolve_db_path(base_dir, args.db)
    output_tree_path = output_dir / "rearrangement_structure_tree.json"
    build_rearranged_structure_tree(plan_path, enriched_path, db_path, output_tree_path)


_STEP_DISPATCH = {
    "enrich": [_step_enrich],
    "backbone": [_step_backbone],
    "match": [_step_match],
    "tree": [_step_tree],
    "all": [_step_enrich, _step_backbone, _step_match, _step_tree],
}


def execute_pipeline_steps(
    context: PipelineContext, base_dir: PathLike, args: argparse.Namespace
) -> None:
    """Run the requested step(s) according to ``args.step``."""
    base = Path(base_dir)
    try:
        for step_fn in _STEP_DISPATCH[args.step]:
            step_fn(context, base, args)
    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()


def run_pipeline_cli(
    args: argparse.Namespace,
    base_dir: Optional[PathLike] = None,
    context: Optional[PipelineContext] = None,
) -> None:
    """CLI entry: ensures dirs, binds debug-log context, runs the dispatched steps."""
    base = Path(base_dir) if base_dir else Path(__file__).resolve().parent
    context = context or _build_context(args, base)
    Path(context.output_dir).mkdir(parents=True, exist_ok=True)
    Path(context.log_dir).mkdir(parents=True, exist_ok=True)

    print(f"Course:  {context.course_name}")
    print(f"Outputs: {context.output_dir}")
    print(f"Debug:   {context.log_dir}")

    token = set_pipeline_log_dir(context.log_dir)
    try:
        execute_pipeline_steps(context, base, args)
    finally:
        reset_pipeline_log_dir(token)


def run_tree_step(
    context: PipelineContext, base_dir: PathLike, args: argparse.Namespace
) -> None:
    """Back-compat shim: previously a separate CLI entry; now just dispatches step=tree."""
    args.step = "tree"
    run_pipeline_cli(args, base_dir=base_dir, context=context)


def _run_pipeline(args: argparse.Namespace) -> None:
    """Backward-compatible name for :func:`run_pipeline_cli`."""
    run_pipeline_cli(args)


def main() -> None:
    args = parse_cli_args()
    base_dir = Path(__file__).resolve().parent
    context = _build_context(args, base_dir)
    run_pipeline_cli(args, base_dir=base_dir, context=context)


if __name__ == "__main__":
    main()
