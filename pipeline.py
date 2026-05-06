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
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

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
    build_tree_from_final_paths,
    generate_rearrangement_plan,
    reorganize_tree_by_final_paths,
    run_backbone_identification,
)
from utils import (
    DEFAULT_LLM_MODEL,
    DEFAULT_LLM_SEED,
    LLMGateway,
    _chunked,
    _derive_course_name,
    _detect_course_prefix,
    configure_cli_logging,
    load_json_file,
    reset_pipeline_log_dir,
    save_debug_log,
    set_pipeline_log_dir,
)

load_dotenv()

log = logging.getLogger(__name__)

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
    input_filename: Optional[str],
    db_filename: Optional[str] = None,
    course_name: Optional[str] = None,
    multi_match: bool = False,
    final_paths_filename: Optional[str] = None,
) -> str:
    """Preprocessing: parse input tree JSON + SQLite DB → enriched JSON.

    Three ways to source the input tree:
      • ``input_filename`` + ``final_paths_filename``  → reorganize the first-pass
        tree using team 1's second-pass routing. Preserves first-pass file
        metadata (e.g. ``file_hash``) when the source is in both files.
        Recommended whenever both files are available.
      • ``final_paths_filename`` only  → build a fresh tree from team 1's
        second-pass doc. No first-pass metadata available.
      • ``input_filename`` only  → load the first-pass tree as-is (legacy mode).

    When ``final_paths_filename`` is supplied, the constructed/reorganized tree
    is also persisted to ``outputs/<course>/v4_tree_reorganized.json`` for
    inspection.
    """
    base = Path(base_dir)

    if db_filename:
        db_file = _resolve_input_path(base, db_filename)
    else:
        db_file = _resolve_db_path(base, None)

    if not course_name:
        course_name = _derive_course_name(db_filename, input_filename)

    output_dir = base / "outputs" / course_name
    output_dir.mkdir(parents=True, exist_ok=True)
    enriched_output = output_dir / "study_enriched.json"

    log.info("=" * 60)
    log.info("Preprocessing: Enriching structure for course '%s'", course_name)
    log.info("=" * 60)
    log.info("Database path: %s", db_file)

    pre_built: Optional[Dict] = None
    input_file_for_log: Optional[Path] = None

    if final_paths_filename:
        final_paths_file = _resolve_input_path(base, final_paths_filename)
        if not final_paths_file.exists():
            raise FileNotFoundError(
                f"--final-paths file not found: {final_paths_file}"
            )
        final_paths_doc = load_json_file(final_paths_file)

        if input_filename:
            # Both files provided: reorganize the first-pass tree using the
            # second-pass routing. Preserves first-pass file metadata
            # (real file_hash, etc.) when the source is in both files.
            tree_file = _resolve_input_path(base, input_filename)
            if not tree_file.exists():
                raise FileNotFoundError(f"--input file not found: {tree_file}")
            log.info("Input tree:      %s", tree_file)
            log.info("Final-paths doc: %s", final_paths_file)
            raw_tree = load_json_file(tree_file)
            pre_built = reorganize_tree_by_final_paths(raw_tree, final_paths_doc)
            input_file_for_log = tree_file
        else:
            # Only final-paths: build from scratch (no first-pass tree available).
            log.info("Final-paths doc: %s", final_paths_file)
            pre_built = build_tree_from_final_paths(final_paths_doc)
            input_file_for_log = final_paths_file

        out = output_dir / "v4_tree_reorganized.json"
        _write_json(out, pre_built, indent=2)
        log.info("Reorganized tree saved to: %s", out)
    else:
        if not input_filename:
            raise ValueError(
                "Either --input or --final-paths must be provided for the 'enrich' step."
            )
        input_file_for_log = _resolve_input_path(base, input_filename)
        log.info("Input file path: %s", input_file_for_log)

    return enrich_structure_with_descriptions(
        str(input_file_for_log) if input_file_for_log else "",
        str(db_file),
        str(enriched_output),
        multi_match=multi_match,
        input_data=pre_built,
    )


def _raw_tree_folder_to_structure_node(
    raw_folder: Dict[str, Any], hash_map: Dict[str, str]
) -> Dict[str, Any]:
    """Convert the v4 reorganized tree shape (children/files dicts) into the
    structure-tree node shape (children list).

    This is intentionally minimal: it preserves hierarchy and populates
    file hashes when possible.
    """

    relative_path = raw_folder.get("relative_path") or raw_folder.get("path") or ""
    node: Dict[str, Any] = {
        "type": "folder",
        "name": raw_folder.get("name") or "",
        "relative_path": relative_path,
        "description": raw_folder.get("folder_description") or "",
        "children": [],
    }

    raw_children = raw_folder.get("children") or {}
    for child_name in sorted(raw_children.keys()):
        child = raw_children[child_name]
        if isinstance(child, dict) and child.get("type") == "folder":
            node["children"].append(_raw_tree_folder_to_structure_node(child, hash_map))

    raw_files = raw_folder.get("files") or {}
    # raw_files is keyed by file_hash in many inputs, but can vary.
    file_nodes: List[Dict[str, Any]] = []
    for file_obj in raw_files.values():
        if not isinstance(file_obj, dict) or file_obj.get("type") != "file":
            continue
        file_rel = file_obj.get("final_path") or file_obj.get("path") or ""
        file_name = file_obj.get("name") or ""
        file_hash = (
            hash_map.get(file_rel)
            or file_obj.get("file_hash")
            or hash_map.get(f"__NAME__{file_name}")
            or ""
        )
        file_node: Dict[str, Any] = {
            "type": "file",
            "name": file_name,
            "relative_path": file_rel,
            "file_hash": file_hash,
        }
        file_nodes.append(file_node)

    file_nodes.sort(key=lambda d: (d.get("name") or "", d.get("relative_path") or ""))
    node["children"].extend(file_nodes)
    return node


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

    log.info("Generated %d backbone groups.", len(backbone_groups))
    save_debug_log([g.model_dump() for g in backbone_groups], "02_2_backbone_groups")

    # --- Step B: Collect orphans and match ---
    orphans = collect_orphan_items(enriched_data, backbone_path, [])
    log.info("Identified %d orphan items needing placement.", len(orphans))
    save_debug_log(orphans, "02_3_orphans_collected")

    if not orphans:
        log.info("No orphans detected.")
        empty = OrphanMatchResponse(matches=[])
        return empty, generate_rearrangement_plan(backbone_groups, empty, llm_gateway=gateway)

    log.info("Sample orphans:")
    for o in orphans[:5]:
        log.info(" - %s (%s)", o["name"], o["type"])

    groups_summary = build_summary(backbone_groups)

    all_matches = []
    chunk_size = 50
    total_orphans = len(orphans)
    total_batches = (total_orphans + chunk_size - 1) // chunk_size
    log.info("Processing %d orphans in %d batches of %d...", total_orphans, total_batches, chunk_size)

    system_prompt = _build_matching_system_prompt(backbone_path, multi_match)
    batch_failures = 0

    for batch_index, batch_orphans in enumerate(_chunked(orphans, chunk_size), start=1):
        log.info("Processing batch %d / %d...", batch_index, total_batches)

        try:
            batch_result = gateway.parse_structured(
                model=DEFAULT_LLM_MODEL,
                system_prompt=system_prompt,
                user_payload={
                    "backbone_folder": backbone_path,
                    "existing_groups": groups_summary,
                    "orphans": batch_orphans,
                },
                response_model=OrphanMatchResponse,
                seed=DEFAULT_LLM_SEED,
            )

            filtered_matches = _filter_matches(batch_result, batch_orphans)
            if filtered_matches:
                all_matches.extend(filtered_matches)
                log.info("  - Matched %d valid items in this batch.", len(filtered_matches))
            else:
                log.warning("No matches returned for batch %d.", batch_index)

        except Exception as e:
            batch_failures += 1
            # Full traceback on first failure (root-cause diagnostic); subsequent
            # failures get a one-liner to avoid 50× traceback spam on a global outage.
            if batch_failures == 1:
                log.exception("Error processing batch %d (showing traceback for first failure only):", batch_index)
            else:
                log.error("Error processing batch %d: %s: %s", batch_index, type(e).__name__, e)

    if batch_failures:
        log.warning(
            "%d/%d batches failed; matched %d/%d orphans before fallback.",
            batch_failures,
            total_batches,
            len(all_matches),
            total_orphans,
        )
        if batch_failures == total_batches:
            raise RuntimeError(
                "All matching batches failed — check OpenAI key, quota, or network."
            )

    matches = OrphanMatchResponse(matches=all_matches)
    unmatched_added = _append_unmatched_orphans_to_misc(orphans, matches.matches)
    if unmatched_added:
        log.warning("Added %d unmatched orphan items to 'Lecture Miscellaneous'.", unmatched_added)
    log.info("Matched total of %d orphan items to groups.", len(matches.matches))

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
        log.warning("Database not found at %s. Hashes will be empty.", db)
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

        log.info("Loaded %d file rows from database. Lookup map size: %d", len(rows), len(result))
        if course_prefix:
            log.info("Detected course prefix: '%s'", course_prefix)
        return result
    except sqlite3.Error as e:
        log.error("Database error: %s", e, exc_info=True)
        return {}
    finally:
        conn.close()


def index_enriched_data(enriched_data: Dict) -> Dict[str, Dict]:
    """Index the enriched tree by path (folder ``relative_path``, file ``source``) and name."""
    index: Dict[str, Dict] = {}

    def traverse(node):
        if node.get("type") == "file":
            path = node.get("source") or node.get("relative_path")
        else:
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
    ntype = original_node.get("type", "folder")
    new_node: Dict[str, Any] = {
        "type": ntype,
        "name": original_node.get("name"),
        "description": original_node.get("description", ""),
    }
    # Carry team-1 routing fields through to the final artifact when present.
    for passthrough in ("final_path", "task_name", "sequence_name"):
        if original_node.get(passthrough) is not None:
            new_node[passthrough] = original_node[passthrough]

    if ntype == "file":
        disk = original_node.get("source") or original_node.get("relative_path") or ""
        if disk:
            new_node["source"] = disk
        fname = new_node.get("name")
        file_hash = hash_map.get(disk)
        if not file_hash and fname:
            file_hash = hash_map.get(f"__NAME__{fname}")
        new_node["file_hash"] = file_hash or ""
    else:
        rp = original_node.get("relative_path")
        if rp:
            new_node["relative_path"] = rp
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

        log.warning("Item not found in enriched data: %s", item_path)

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
    plan_path: PathLike,
    enriched_data_path: PathLike,
    db_path: PathLike,
    output_path: PathLike,
    reorganized_tree: Optional[Dict[str, Any]] = None,
) -> None:
    """Build a full hierarchical tree based on the rearrangement plan.

    Raises FileNotFoundError / ValueError on missing or malformed inputs;
    the orchestrator is responsible for surfacing the failure to the user.
    """
    log.info("Building rearranged structure tree...")

    plan_data = load_json_file(str(plan_path))
    enriched_data = load_json_file(str(enriched_data_path))
    hash_map = load_file_hashes(db_path)

    enriched_index = index_enriched_data(enriched_data)

    plan_groups = _load_plan_groups(plan_data)
    if not plan_groups:
        raise ValueError(
            f"Invalid plan format at {plan_path}: expected a list or {{'groups': [...]}}."
        )

    study_children = [_build_group_node(g, enriched_index, hash_map) for g in plan_groups]

    # Keep the artifact as a list of group nodes, but ensure all rearranged
    # study content is under one top-level group.
    result_tree = [
        {
            "type": "group",
            "name": "Study",
            "children": study_children,
        }
    ]

    # Append non-study sections (practice/, support/) from the reorganized tree
    # when available. IMPORTANT: we do not implicitly read intermediate outputs
    # as inputs. Callers should pass the reorganized tree (preferred), or an
    # explicit path if needed.
    try:
        if isinstance(reorganized_tree, dict):
            raw_children = reorganized_tree.get("children") or {}

            for section_key in ("practice", "support"):
                raw_section = raw_children.get(section_key)
                if isinstance(raw_section, dict) and raw_section.get("type") == "folder":
                    section_node = _raw_tree_folder_to_structure_node(raw_section, hash_map)
                    result_tree.append(
                        {
                            "type": "group",
                            "name": section_key.capitalize(),
                            "children": [section_node],
                        }
                    )
    except Exception as e:
        log.warning("Failed to append practice/support sections: %s", e, exc_info=True)

    _write_json(Path(output_path), result_tree, indent=2)
    log.info("Rearranged structure tree saved to: %s", output_path)


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


def _step_enrich(context: PipelineContext, base_dir: Path, args: argparse.Namespace) -> None:
    final_paths = getattr(args, "final_paths", None)
    if not args.input and not final_paths:
        raise ValueError(
            "Provide either --input (first-pass tree) or --final-paths "
            "(team-1 second-pass doc) for the 'enrich' / 'all' steps."
        )
    run_enrichment(
        base_dir,
        args.input,
        args.db,
        context.course_name,
        multi_match=context.multi_match,
        final_paths_filename=final_paths,
    )


def _step_backbone(context: PipelineContext, base_dir: Path, args: argparse.Namespace) -> None:
    log.info("=" * 60)
    log.info("Step 1: Backbone Identification")
    log.info("=" * 60)
    enriched_data = _load_enriched(base_dir, context.course_name)
    backbone_path = run_backbone_identification(enriched_data)

    backbone_output = Path(context.output_dir) / "backbone_result.json"
    _write_json(backbone_output, {"backbone_path": backbone_path})
    log.info("Backbone result saved to: %s", backbone_output)


def _step_match(context: PipelineContext, base_dir: Path, args: argparse.Namespace) -> None:
    log.info("=" * 60)
    log.info("Step 2: Orphan Matching")
    log.info("=" * 60)
    enriched_data = _load_enriched(base_dir, context.course_name)

    backbone_file = Path(context.output_dir) / "backbone_result.json"
    if backbone_file.exists():
        backbone_path = load_json_file(str(backbone_file))["backbone_path"]
    else:
        log.info("No backbone result found, running backbone identification first...")
        backbone_path = run_backbone_identification(enriched_data)
        # Persist so re-runs of `match` don't re-pay the LLM call.
        _write_json(backbone_file, {"backbone_path": backbone_path})
        log.info("Backbone result saved to: %s", backbone_file)

    matches, plan = run_plan_matching(
        enriched_data,
        backbone_path,
        multi_match=context.multi_match,
    )

    matches_output = Path(context.output_dir) / "orphan_matches.json"
    _write_json(matches_output, matches.model_dump())
    log.info("Orphan matches saved to: %s", matches_output)

    plan_output = Path(context.output_dir) / "rearrangement_plan.json"
    _write_json(plan_output, plan)
    log.info("Rearrangement plan saved to: %s", plan_output)


def _step_tree(context: PipelineContext, base_dir: Path, args: argparse.Namespace) -> None:
    log.info("=" * 60)
    log.info("Step 3: Tree Materialization")
    log.info("=" * 60)
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

    # Source the reorganized full-course tree (used to append practice/support
    # top-level groups). Resolution order:
    #   1. Re-build from original inputs when --final-paths is supplied (and
    #      --input, for the reorganize path that preserves first-pass metadata).
    #   2. Fall back to the persisted v4_tree_reorganized.json artifact written
    #      by `enrich`. This is the *only* downstream step that reads back from
    #      outputs/, and it does so as a cache — never as a pipeline input.
    reorganized_tree: Optional[Dict[str, Any]] = None
    try:
        final_paths = getattr(args, "final_paths", None)
        if final_paths:
            final_paths_file = _resolve_input_path(base_dir, final_paths)
            final_paths_doc = load_json_file(final_paths_file)
            if args.input:
                tree_file = _resolve_input_path(base_dir, args.input)
                raw_tree = load_json_file(tree_file)
                reorganized_tree = reorganize_tree_by_final_paths(raw_tree, final_paths_doc)
            else:
                reorganized_tree = build_tree_from_final_paths(final_paths_doc)
        else:
            cached = output_dir / "v4_tree_reorganized.json"
            if cached.exists():
                log.info("Loading cached reorganized tree from: %s", cached)
                reorganized_tree = load_json_file(str(cached))
    except Exception as e:
        log.warning("Failed to obtain reorganized tree for practice/support append: %s", e, exc_info=True)

    build_rearranged_structure_tree(
        plan_path,
        enriched_path,
        db_path,
        output_tree_path,
        reorganized_tree=reorganized_tree,
    )


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
    """Run the requested step(s) according to ``args.step``.

    Any step exception is logged and re-raised so the caller can fail fast
    (and so ``--step all`` doesn't silently continue against stale artifacts).
    """
    base = Path(base_dir)
    for step_fn in _STEP_DISPATCH[args.step]:
        try:
            step_fn(context, base, args)
        except Exception:
            log.exception("Error in step '%s'", args.step)
            raise


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

    log.info("Course:  %s", context.course_name)
    log.info("Outputs: %s", context.output_dir)
    log.info("Debug:   %s", context.log_dir)

    token = set_pipeline_log_dir(context.log_dir)
    try:
        execute_pipeline_steps(context, base, args)
    finally:
        reset_pipeline_log_dir(token)


def run_tree_step(
    context: PipelineContext, base_dir: PathLike, args: argparse.Namespace
) -> None:
    """Back-compat shim for callers that used the standalone tree entry.

    Calls the tree step directly without mutating ``args`` or re-binding the
    log ContextVar / re-printing the header (which ``run_pipeline_cli`` does).
    Assumes the caller has already entered an enclosing ``run_pipeline_cli`` /
    ``set_pipeline_log_dir`` context.
    """
    _step_tree(context, Path(base_dir), args)


def _run_pipeline(args: argparse.Namespace) -> None:
    """Backward-compatible name for :func:`run_pipeline_cli`."""
    run_pipeline_cli(args)


def main() -> None:
    configure_cli_logging()
    args = parse_cli_args()
    base_dir = Path(__file__).resolve().parent
    context = _build_context(args, base_dir)
    try:
        run_pipeline_cli(args, base_dir=base_dir, context=context)
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    main()
