"""Back-compat re-export shim.

The implementation now lives in four focused modules — ``models``, ``utils``,
``steps``, ``pipeline``. New code should import from those directly. Existing
call sites can keep importing from ``file_rearrang`` until migrated.
"""

from models import (  # noqa: F401
    BackboneGroup,
    BackboneResult,
    FileDescription,
    MiscGroupAssignment,
    MiscRefinementResponse,
    OrphanMatch,
    OrphanMatchResponse,
    PipelineContext,
)
from pipeline import (  # noqa: F401
    _build_context,
    _build_group_node,
    _dedupe_items,
    _load_enriched,
    _load_plan_groups,
    _resolve_db_path,
    _resolve_original_node,
    _run_pipeline,
    build_arg_parser,
    build_node_recursive,
    build_rearranged_structure_tree,
    execute_pipeline_steps,
    index_enriched_data,
    load_file_hashes,
    main,
    parse_cli_args,
    run_enrichment,
    run_file_rearrangement,
    run_pipeline_cli,
    run_plan_matching,
    run_tree_step,
)
from pydantic import BaseModel  # noqa: F401  # historical re-export
from steps import (  # noqa: F401
    _append_unmatched_orphans_to_misc,
    _build_matching_system_prompt,
    _enrich_merge_practice_into_study,
    _enrich_process_children,
    _enrich_rebase_paths_under_prefix,
    _enrich_resolve_relative_path,
    _enrich_should_keep_branch,
    _enrich_should_skip_file,
    _filter_matches,
    _make_backbone_groups,
    _orphan_append_leaf_unit,
    _orphan_append_manual_aggregate,
    _orphan_build_leaf_folder_unit_description,
    _orphan_leaf_folder_auto_aggregate,
    _orphan_skip_backbone_subtree,
    aggregate_folder_descriptions,
    build_summary,
    build_tree_from_final_paths,
    collect_orphan_items,
    enrich_structure_with_descriptions,
    extract_backbone_subtree,
    extract_file_descriptions,
    generate_rearrangement_plan,
    merge_final_paths_into_tree,
    reorganize_tree_by_final_paths,
    run_backbone_identification,
)
from utils import (  # noqa: F401
    DEFAULT_LLM_MODEL,
    DEFAULT_LLM_SEED,
    LLMGateway,
    _chunked,
    _console_safe,
    _derive_course_name,
    _detect_course_prefix,
    _is_under_path,
    _llm_parse,
    _normalize_path,
    _safe_print,
    load_json_file,
    reset_pipeline_log_dir,
    save_debug_log,
    set_pipeline_log_dir,
)


if __name__ == "__main__":
    main()
