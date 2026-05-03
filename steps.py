"""Domain logic for the rearrangement pipeline.

Sections (in pipeline order):
  1. Enrichment       — walk the input tree, attach descriptions from the metadata DB.
  2. Backbone         — identify the chronological backbone folder, build groups + prompt.
  3. Orphan collection — gather non-backbone items + filter LLM matches.
  4. Plan generation  — merge backbone groups + matches into the final plan.
"""

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from models import (
    BackboneGroup,
    BackboneResult,
    FileDescription,
    OrphanMatch,
    OrphanMatchResponse,
    RearrangedGroup,
)
from utils import (
    LLMGateway,
    _is_under_path,
    _normalize_path,
    _safe_print,
    save_debug_log,
)


# =============================================================================
# 1. Enrichment
# =============================================================================

@dataclass
class _EnrichmentStats:
    processed: int = 0
    enriched: int = 0


def _enrich_should_keep_branch(
    category: str, node_name: str, parent_force_keep: bool, multi_match: bool
) -> bool:
    """Whether this node's subtree is eligible for study/practice retention."""
    is_study = category == "study"
    if multi_match and (category.lower() == "practice" or node_name.lower() == "practice"):
        is_study = True
    return is_study or parent_force_keep


def _enrich_should_skip_file(filename: str) -> bool:
    lower_name = filename.lower()
    return lower_name.endswith(".yaml") or lower_name.endswith(".json")


def _enrich_resolve_relative_path(
    node_data: Dict, node_name: str, current_path: str
) -> str:
    """Compute anchored relative_path and map legacy ``root`` prefix to ``study``."""
    rel_path = node_data.get("relative_path")
    default_rel_path = f"{current_path}/{node_name}" if current_path else node_name
    if not rel_path:
        rel_path = default_rel_path
    elif current_path and not rel_path.startswith(current_path):
        rel_path = default_rel_path
    if rel_path.startswith("root"):
        rel_path = "study" + rel_path[4:]
    return rel_path


def _enrich_rebase_paths_under_prefix(node: Dict, old_prefix: str, new_prefix: str) -> None:
    path = node.get("relative_path")
    if path and path.startswith(old_prefix):
        node["relative_path"] = new_prefix + path[len(old_prefix):]
    for child in node.get("children", []) or []:
        _enrich_rebase_paths_under_prefix(child, old_prefix, new_prefix)


def _enrich_merge_practice_into_study(enriched_root: Dict) -> None:
    """When multi_match: move practice subtree under study and rebase paths."""
    children = enriched_root.get("children", []) or []
    practice_node = None
    study_node = None
    for child in children:
        if child.get("type") == "folder" and child.get("name", "").lower() == "practice":
            practice_node = child
        if child.get("type") == "folder" and child.get("name", "").lower() == "study":
            study_node = child

    if practice_node and study_node:
        old_prefix = f"{practice_node.get('relative_path', '').rstrip('/')}/"
        new_prefix = f"{study_node.get('relative_path', '').rstrip('/')}/"
        for child in practice_node.get("children", []) or []:
            _enrich_rebase_paths_under_prefix(child, old_prefix, new_prefix)
            study_node["children"].append(child)
        children.remove(practice_node)
        enriched_root["children"] = children


def _enrich_process_children(
    node_data: Dict,
    rel_path: str,
    should_keep_this: bool,
    process_node,
) -> List[Dict]:
    """Flatten ``children`` (dict or list) and ``files`` dict into processed child nodes."""
    kept: List[Dict] = []
    children_dict = node_data.get("children", {})
    if isinstance(children_dict, dict):
        for child_name, child_data in children_dict.items():
            child_processed = process_node(
                child_data, child_name, rel_path, parent_force_keep=should_keep_this
            )
            if child_processed:
                kept.append(child_processed)
    elif isinstance(children_dict, list):
        for child_data in children_dict:
            cname = child_data.get("name", "unknown")
            child_processed = process_node(
                child_data, cname, rel_path, parent_force_keep=should_keep_this
            )
            if child_processed:
                kept.append(child_processed)

    files_dict = node_data.get("files", {})
    if isinstance(files_dict, dict):
        for _file_hash, file_data in files_dict.items():
            fname = file_data.get("name", "unknown")
            file_processed = process_node(
                file_data, fname, rel_path, parent_force_keep=should_keep_this
            )
            if file_processed:
                kept.append(file_processed)
    return kept


def enrich_structure_with_descriptions(
    input_json_path: str, db_path: str, output_path: str, multi_match: bool = False
) -> str:
    """Parse the input tree JSON, filter for 'study' category, fetch descriptions
    from database, and generate a standardized list-based JSON tree."""
    print(f"Reading input structure from: {input_json_path}")

    input_path = Path(input_json_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    with input_path.open("r", encoding="utf-8") as f:
        input_data = json.load(f)

    db = Path(db_path)
    if not db.exists():
        raise FileNotFoundError(f"Database not found at: {db}")

    conn = sqlite3.connect(str(db))
    cursor = conn.cursor()
    stats = _EnrichmentStats()

    def get_file_description(filename: str):
        cursor.execute(
            "SELECT description FROM file WHERE file_name = ? LIMIT 1",
            (filename,),
        )
        result = cursor.fetchone()
        if result and result[0]:
            return result[0].strip()
        escaped = filename.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        cursor.execute(
            "SELECT description FROM file WHERE relative_path LIKE ? ESCAPE '\\' LIMIT 1",
            (f"%/{escaped}",),
        )
        result = cursor.fetchone()
        return result[0].strip() if result and result[0] else None

    def process_node(
        node_data: Dict,
        node_name: str,
        current_path: str = "",
        parent_force_keep: bool = False,
    ):
        node_type = node_data.get("type", "folder")
        category = node_data.get("category", "")
        should_keep_this = _enrich_should_keep_branch(
            category, node_name, parent_force_keep, multi_match
        )
        rel_path = _enrich_resolve_relative_path(node_data, node_name, current_path)

        if node_type == "file":
            filename = node_data.get("name", node_name)
            if _enrich_should_skip_file(filename):
                return None
            new_node: Dict = {
                "type": "file",
                "name": filename,
                "relative_path": rel_path,
            }
            desc = get_file_description(filename)
            if desc:
                new_node["description"] = desc
                stats.enriched += 1
            stats.processed += 1
            return new_node if should_keep_this else None

        new_node = {
            "type": "folder",
            "name": node_name,
            "relative_path": rel_path,
            "children": [],
        }
        by_sequence = node_data.get("by_sequence")
        if by_sequence is not None:
            new_node["by_sequence"] = by_sequence

        new_node["children"] = _enrich_process_children(
            node_data, rel_path, should_keep_this, process_node
        )

        if should_keep_this:
            return new_node
        if len(new_node["children"]) > 0:
            return new_node
        return None

    root_name = input_data.get("name", "root")
    enriched_root = process_node(input_data, root_name, "")
    conn.close()

    if enriched_root:
        enriched_root["name"] = "study"
        enriched_root["relative_path"] = "study"
        if multi_match:
            _enrich_merge_practice_into_study(enriched_root)

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as f:
            json.dump(enriched_root, f, indent=2, ensure_ascii=False)
        print(f"Enriched JSON structure saved to: {out}")
        print(
            f"Processed {stats.processed} files, enriched {stats.enriched} with descriptions"
        )
        return output_path

    print("Warning: No 'study' content found in input tree.")
    return ""


def extract_file_descriptions(enriched_data: Dict) -> List[FileDescription]:
    """Recursively walk the enriched JSON tree and extract file descriptions."""
    descriptions: List[FileDescription] = []

    def traverse(node: Dict, folder_path: str = ""):
        children = node.get("children", []) or []
        name = node.get("name", "")
        node_type = node.get("type", "folder")

        if node_type == "folder":
            current_path = f"{folder_path}/{name}" if folder_path else name
            for child in children:
                traverse(child, current_path)
        elif node_type == "file":
            desc = node.get("description", "")
            if desc:
                descriptions.append(
                    FileDescription(
                        relative_folder_path=folder_path,
                        file=name,
                        description=desc,
                    )
                )

    for child in enriched_data.get("children", []) or []:
        traverse(child)

    return descriptions


# =============================================================================
# 2. Backbone identification + matching prompt
# =============================================================================

def extract_backbone_subtree(enriched_data: Dict, backbone_path: str) -> Optional[Dict]:
    """Find and return the backbone folder node from the enriched tree."""
    backbone_path = _normalize_path(backbone_path)

    def find(node: Dict, hierarchy_path: str = "") -> Optional[Dict]:
        name = node.get("name", "")
        children = node.get("children", []) or []

        node_path = f"{hierarchy_path}/{name}" if hierarchy_path else name

        if not name:
            for child in children:
                result = find(child, hierarchy_path)
                if result:
                    return result
            return None

        normalized_node_path = _normalize_path(node_path)
        if normalized_node_path == backbone_path:
            return node

        if backbone_path.startswith(normalized_node_path + "/"):
            for child in children:
                result = find(child, node_path)
                if result:
                    return result

        return None

    for child in enriched_data.get("children", []) or []:
        result = find(child)
        if result:
            return result
    return None


def _make_backbone_groups(backbone_subtree: Dict) -> List[BackboneGroup]:
    backbone_groups: List[BackboneGroup] = []

    for child in backbone_subtree.get("children", []) or []:
        name = child.get("name", "Unknown")
        rel_path = child.get("relative_path", name)

        raw_desc = child.get("description", "").strip()
        if not raw_desc and child.get("type") == "folder":
            raw_desc = aggregate_folder_descriptions(child)

        backbone_groups.append(
            BackboneGroup(
                group_name=name,
                main_item=rel_path,
                related_items=[],
                description=raw_desc or f"Topic material for {name}",
            )
        )

    backbone_groups.append(
        BackboneGroup(
            group_name="Lecture Miscellaneous",
            main_item="",
            related_items=[],
            description="Miscellaneous materials that do not fit perfectly into other units.",
        )
    )

    return backbone_groups


def _build_matching_system_prompt(backbone_path: str, multi_match: bool) -> str:
    if multi_match:
        return (
            f"You are an intelligent course material organizer for any subject (Computer Science, Math, Literature, etc.).\n\n"
            f"The folder '{backbone_path}' defines the chronological 'backbone' of this course.\n"
            f"You will receive:\n"
            f"- A list of 'Existing Groups' (the backbone units) with their descriptions.\n"
            f"- A batch of 'Orphan Files' that need to be categorized.\n\n"
            f"Your Task:\n"
            f"For EACH orphan, assign it to its relevant group. If you think this material can have match to multiple topic lecture groups, match ALL of them that is relevant.\n\n"
            f"Topic-Only Mapping Rule (Critical):\n"
            f"- Assign based on actual lecture topic coverage only (topic concepts/skills in the description and group description are related).\n"
            f"Matching Considerations:\n"
            f"1. **Strong Match (Preferred)**: If the file's name or description strongly relates to a specific backbone unit's topic/descriptions.\n"
            f"   - Example: A file focusing on both recursion and tree recursion fits into 'Lecture XX: recursion' and 'Lecture XX: tree recursion' if the both topic is include in the group description.\n"
            f"2. **Ambiguous/No Match (Fallback)**: If the file does not clearly fit any specific backbone unit, place it in 'Lecture Miscellaneous' category\n"
            f"NOTE: Try to infer its topic from its description. If the Orphan description is not informative, it's safer to put it in Miscellaneous than to risk misplacement.\n"
            f"Constraints:\n"
            f"- Use existing 'group_name' exactly as provided when matching.\n"
            f"- Every single orphan file MUST be assigned to AT LEAST one group.\n"
            f"- Do NOT create files that do not exist in orphans.\n"
        )

    return (
        f"You are an intelligent course material organizer for any subject (Computer Science, Math, Literature, etc.).\n\n"
        f"The folder '{backbone_path}' defines the chronological 'backbone' of this course.\n"
        f"You will receive:\n"
        f"- A list of 'Existing Groups' (the backbone units) with their descriptions.\n"
        f"- A batch of 'Orphan Files' that need to be categorized.\n\n"
        f"Your Task:\n"
        f"For EACH orphan, assign it to the most semantically relevant group. If you think this material can have multiple matches, assign it to the most relevant one.\n\n"
        f"Matching Considerations:\n"
        f"1. **Strong Match (Preferred)**: If the file's name or description strongly relates to a specific backbone unit's topic/descriptions.\n"
        f"   - Example: A file named 'Derivatives Practice' or on Derivatives fits into 'Lecture XX: Differentiation' or Derivatives topic is include in the group description.\n"
        f"2. **Ambiguous/No Match (Fallback)**: If the file does not clearly fit any specific backbone unit, place it in 'Lecture Miscellaneous' category\n"
        f"NOTE: Try to infer its topic from its name. If the Orphan description and the name is not informative, it's safer to put it in Miscellaneous than to risk misplacement.\n"
        f"Constraints:\n"
        f"- Use existing 'group_name' exactly as provided when matching.\n"
        f"- Do NOT create files that do not exist in orphans.\n"
    )


def run_backbone_identification(
    enriched_data: Dict,
    *,
    llm_gateway: Optional[LLMGateway] = None,
) -> str:
    """Identify the main backbone folder from the enriched structure."""
    file_descriptions = extract_file_descriptions(enriched_data)

    descriptions_payload = [fd.model_dump() for fd in file_descriptions]
    save_debug_log(descriptions_payload, "01_backbone_descriptions_payload")

    gateway = llm_gateway or LLMGateway()
    system_prompt = (
        "You are an intelligent file system organizer for university course materials.\n"
        "Given the study folder structure and each file description, "
        "identify the 'Main Type' folder that best serves as the chronological "
        "backbone of the course.\n"
        "The backbone should be the folder containing core lecture materials "
        "that provides the main chronological structure for the course (etc. Lecture Slides).\n"
        "Return only the relative folder path of the backbone."
    )

    result: BackboneResult = gateway.parse_structured(
        model="gpt-5-mini",
        system_prompt=system_prompt,
        user_payload=descriptions_payload,
        response_model=BackboneResult,
        seed=42,
    )
    print(f"Identified backbone: {result.backbone_path}")
    return result.backbone_path


# =============================================================================
# 3. Orphan collection + post-LLM filtering
# =============================================================================

def aggregate_folder_descriptions(node: Dict, max_files: int = 5) -> str:
    """Recursively collect descriptions from child files to represent a folder."""
    descriptions = []
    own_desc = node.get("description", "").strip()
    if own_desc:
        descriptions.append(f"Folder: {own_desc}")

    count = 0
    stack = [node]

    while stack and count < max_files:
        curr = stack.pop()
        children = curr.get("children", []) or []
        for child in reversed(children):
            if child.get("type") == "file":
                d = child.get("description", "").strip()
                n = child.get("name")
                if d and count < max_files:
                    descriptions.append(f"{n}: {d}")
                    count += 1
            elif child.get("type") == "folder":
                stack.append(child)

    return " | ".join(descriptions)


def get_folder_candidates(enriched_data: Dict, backbone_path: str) -> List[Dict]:
    """Identify folders that are candidates for aggregation (orphans only)."""
    candidates = []
    backbone_path = _normalize_path(backbone_path)

    def traverse(node: Dict, hierarchy_path: str = ""):
        name = node.get("name")
        children = node.get("children", []) or []

        if not name:
            for child in children:
                traverse(child, hierarchy_path)
            return

        node_path = f"{hierarchy_path}/{name}" if hierarchy_path else name

        if _is_under_path(node_path, backbone_path):
            return

        if _normalize_path(backbone_path).startswith(f"{_normalize_path(node_path)}/"):
            for child in children:
                traverse(child, node_path)
            return

        node_type = node.get("type", "folder")

        if node_type == "folder":
            file_children = [c.get("name") for c in children if c.get("type") == "file"]
            folder_children = [c.get("name") for c in children if c.get("type") == "folder"]
            has_subfolders = len(folder_children) > 0

            if file_children and not has_subfolders:
                candidates.append(
                    {
                        "path": node_path,
                        "num_files": len(file_children),
                        "num_subfolders": len(folder_children),
                        "sample_files": file_children[:5],
                        "sample_subfolders": folder_children[:5],
                        "folder_description": aggregate_folder_descriptions(node),
                    }
                )

            for child in children:
                traverse(child, node_path)

    for child in enriched_data.get("children", []) or []:
        traverse(child)

    return candidates


def _orphan_skip_backbone_subtree(node_path: str, backbone_folder: str) -> bool:
    if _normalize_path(node_path) == backbone_folder:
        return True
    return _is_under_path(node_path, backbone_folder)


def _orphan_leaf_folder_auto_aggregate(node: Dict) -> bool:
    children = node.get("children", []) or []
    folder_children = [c for c in children if c.get("type") == "folder"]
    file_children = [c for c in children if c.get("type") == "file"]
    is_sequential = node.get("by_sequence", False)
    return (
        len(folder_children) == 0
        and len(file_children) > 0
        and not is_sequential
    )


def _orphan_build_leaf_folder_unit_description(node: Dict) -> str:
    children = node.get("children", []) or []
    file_children = [c for c in children if c.get("type") == "file"]
    combined_desc = f"Folder containing: {', '.join([c.get('name') for c in file_children])}"
    rich_descs = [
        f"{c.get('name')}: {c.get('description', '')}"
        for c in file_children
        if c.get("description")
    ]
    if rich_descs:
        combined_desc += ". Details: " + " | ".join(rich_descs)
    return combined_desc


def _orphan_append_leaf_unit(
    orphans: List[Dict], node: Dict, node_path: str, name: str
) -> None:
    orphans.append(
        {
            "structure_path": node_path,
            "relative_path": node.get("relative_path", node_path),
            "name": name,
            "type": "folder_unit",
            "description": _orphan_build_leaf_folder_unit_description(node),
        }
    )


def _orphan_append_manual_aggregate(
    orphans: List[Dict], node: Dict, node_path: str, name: str, backbone_path: str
) -> bool:
    """Return True if appended and recursion should stop."""
    if _normalize_path(backbone_path).startswith(f"{_normalize_path(node_path)}/"):
        return False
    orphans.append(
        {
            "structure_path": node_path,
            "relative_path": node.get("relative_path", node_path),
            "name": name,
            "type": "folder (aggregated)",
            "description": aggregate_folder_descriptions(node),
        }
    )
    return True


def collect_orphan_items(
    enriched_data: Dict, backbone_path: str, aggregated_paths: Optional[List[str]] = None
) -> List[Dict]:
    """Collect items that are NOT in the backbone folder."""
    orphans: List[Dict] = []
    aggregated_paths_set = set(aggregated_paths or [])
    backbone_folder = _normalize_path(backbone_path)

    def traverse(node: Dict, hierarchy_path: str = "") -> None:
        name = node.get("name")
        node_type = node.get("type", "folder")
        children = node.get("children", []) or []

        if not name:
            for child in children:
                traverse(child, hierarchy_path)
            return

        node_path = f"{hierarchy_path}/{name}" if hierarchy_path else name

        if _orphan_skip_backbone_subtree(node_path, backbone_folder):
            return

        description = node.get("description", "")

        if node_type == "folder" and _orphan_leaf_folder_auto_aggregate(node):
            _orphan_append_leaf_unit(orphans, node, node_path, name)
            return

        if node_type == "folder" and node_path in aggregated_paths_set:
            if _orphan_append_manual_aggregate(orphans, node, node_path, name, backbone_path):
                return

        if node_type == "folder":
            if not children:
                return
            for child in children:
                traverse(child, node_path)
            return

        orphans.append(
            {
                "structure_path": node_path,
                "relative_path": node.get("relative_path", node_path),
                "name": name,
                "type": node_type,
                "description": description,
            }
        )

    for child in enriched_data.get("children", []) or []:
        traverse(child)

    return orphans


def build_summary(
    items: List[Any],
    limit: Optional[int] = None,
    truncate_fields: Optional[Dict[str, int]] = None,
) -> List[Dict]:
    """Dynamically build a summary list from Pydantic models or dictionaries."""
    if not items:
        return []

    slice_end = limit if limit is not None else len(items)
    items_slice = items[:slice_end]

    summary = []
    for item in items_slice:
        if isinstance(item, BaseModel):
            data = item.model_dump()
        elif isinstance(item, dict):
            data = item.copy()
        else:
            continue

        if truncate_fields:
            for field, max_len in truncate_fields.items():
                val = data.get(field)
                if isinstance(val, str) and len(val) > max_len:
                    data[field] = val[:max_len] + "..."

        summary.append(data)

    return summary


def _filter_matches(
    batch_result: OrphanMatchResponse, batch_orphans: List[Dict]
) -> List[OrphanMatch]:
    if not batch_result or not batch_result.matches:
        return []

    valid_orphan_paths: set = set()
    normalized_to_original: Dict[str, str] = {}
    basename_to_orphans: Dict[str, List[str]] = {}

    for o in batch_orphans:
        canonical = o.get("relative_path") or o.get("structure_path")
        if not canonical:
            continue
        for key in ("relative_path", "structure_path"):
            p = o.get(key)
            if p:
                valid_orphan_paths.add(p)
                normalized_to_original[re.sub(r"\s+", " ", p).strip()] = canonical
        for hint in (o.get("name"), Path(canonical).name):
            if hint:
                basename_to_orphans.setdefault(hint, []).append(canonical)

    filtered_matches: List[OrphanMatch] = []
    warned_paths: set = set()
    fixed_paths: set = set()

    for match in batch_result.matches:
        cleaned_path = match.item_path.strip()

        if cleaned_path in valid_orphan_paths:
            filtered_matches.append(match)
            continue

        normalized = re.sub(r"\s+", " ", cleaned_path)
        if normalized in normalized_to_original:
            match.item_path = normalized_to_original[normalized]
            filtered_matches.append(match)
            if cleaned_path not in fixed_paths:
                fixed_paths.add(cleaned_path)
                _safe_print(f"  - Fixed LLM path: '{cleaned_path}' -> '{match.item_path}'")
            continue

        # Basename rescue: LLM often drops/adds intermediate folders. Accept iff unique.
        leaf = Path(cleaned_path).name
        candidates = list(dict.fromkeys(basename_to_orphans.get(leaf, [])))
        if len(candidates) == 1:
            resolved = candidates[0]
            match.item_path = resolved
            filtered_matches.append(match)
            if cleaned_path not in fixed_paths:
                fixed_paths.add(cleaned_path)
                _safe_print(f"  - Rescued by basename: '{cleaned_path}' -> '{resolved}'")
            continue

        if cleaned_path in warned_paths:
            continue
        warned_paths.add(cleaned_path)
        if len(candidates) > 1:
            _safe_print(
                f"  - Warning: Ambiguous basename '{leaf}' matches {len(candidates)} orphans, dropping: {cleaned_path}"
            )
        else:
            _safe_print(f"  - Warning: Filtered out hallucinated item: {cleaned_path}")

    return filtered_matches


def _append_unmatched_orphans_to_misc(
    all_orphans: List[Dict],
    all_matches: List[OrphanMatch],
    *,
    fallback_group: str = "Lecture Miscellaneous",
) -> int:
    """Ensure every orphan is represented by assigning unmatched items to fallback group."""
    if not all_orphans:
        return 0

    matched_paths = {m.item_path.strip() for m in all_matches if m.item_path}
    appended = 0

    for orphan in all_orphans:
        candidate_paths = []
        rel_path = orphan.get("relative_path")
        if rel_path:
            candidate_paths.append(rel_path)
        structure_path = orphan.get("structure_path")
        if structure_path and structure_path not in candidate_paths:
            candidate_paths.append(structure_path)

        if any(path in matched_paths for path in candidate_paths):
            continue

        fallback_path = rel_path or structure_path
        if not fallback_path:
            continue

        all_matches.append(
            OrphanMatch(item_path=fallback_path, assigned_group=fallback_group)
        )
        matched_paths.add(fallback_path)
        appended += 1

    return appended


# =============================================================================
# 4. Plan generation
# =============================================================================

def _normalize_key(name: str) -> str:
    return name.strip().lower()


def _init_plan_from_backbone(backbone_groups: List[BackboneGroup]) -> Dict[str, Dict]:
    plan_map: Dict[str, Dict] = {}
    for bg in backbone_groups:
        key = _normalize_key(bg.group_name)

        if key not in plan_map:
            initial_related = getattr(bg, "related_items", []) or []
            plan_map[key] = {
                "group_name": bg.group_name,
                "main_item": bg.main_item,
                "description": bg.description,
                "related_items": list(initial_related),
            }
            continue

        existing_entry = plan_map[key]
        for item in (getattr(bg, "related_items", []) or []):
            if item not in existing_entry["related_items"]:
                existing_entry["related_items"].append(item)

        if bg.description:
            if existing_entry["description"] and existing_entry["description"] != bg.description:
                existing_entry["description"] += f" | {bg.description}"
            elif not existing_entry["description"]:
                existing_entry["description"] = bg.description

        if not existing_entry["main_item"] and bg.main_item:
            existing_entry["main_item"] = bg.main_item

    return plan_map


def _distribute_matches(plan_map: Dict[str, Dict], matches: OrphanMatchResponse) -> None:
    for match in matches.matches:
        raw_target_group = match.assigned_group.strip()
        orphan_path = match.item_path.strip()

        # LLM outputs sometimes use different separators for multi-matches.
        # Support both comma and semicolon.
        normalized_target_group = raw_target_group.replace(";", ",")
        target_groups = [g.strip() for g in normalized_target_group.split(",") if g.strip()]
        if not target_groups:
            target_groups = [raw_target_group] if raw_target_group else []

        for target_group in target_groups:
            target_group_display = target_group
            if target_group_display.lower().startswith("new:"):
                target_group_display = target_group_display.split(":", 1)[1].strip()

            if not target_group_display:
                continue

            key = _normalize_key(target_group_display)

            if key not in plan_map:
                plan_map[key] = {
                    "group_name": target_group_display,
                    "main_item": None,
                    "description": "Dynamically created group",
                    "related_items": [],
                }

            related = plan_map[key]["related_items"]
            if orphan_path not in related:
                related.append(orphan_path)


def _refine_misc_group(
    plan_map: Dict[str, Dict], gateway: LLMGateway, *, seed: int = 42
) -> None:
    misc_key = _normalize_key("Lecture Miscellaneous")
    if misc_key not in plan_map or not plan_map[misc_key]["related_items"]:
        return

    misc_items = plan_map[misc_key]["related_items"]
    print(f"Refining {len(misc_items)} items in the Lecture Miscellaneous folder...")

    misc_payload = [
        {"item_path": path, "filename": path.split("/")[-1]} for path in misc_items
    ]

    try:
        refined_result = gateway.refine_miscellaneous_groups(misc_payload, seed=seed)
    except Exception as e:
        print(f"Failed to refine Miscellaneous folder: {e}")
        return

    plan_map[misc_key]["related_items"] = []

    for assignment in refined_result.assignments:
        new_key = _normalize_key(assignment.new_group_name)
        if new_key not in plan_map:
            plan_map[new_key] = {
                "group_name": assignment.new_group_name,
                "main_item": "",
                "description": assignment.new_group_description,
                "related_items": [],
            }
        if assignment.item_path not in plan_map[new_key]["related_items"]:
            plan_map[new_key]["related_items"].append(assignment.item_path)

    print("Successfully refined the Miscellaneous folder into specific categories.")


def _serialize_plan(plan_map: Dict[str, Dict]) -> List[Dict]:
    final_plan = []
    for p in plan_map.values():
        main_item = p.get("main_item") or ""
        final_plan.append(
            {
                "group_name": p["group_name"],
                "main_item": main_item,
                "related_items": p["related_items"],
                "description": p.get("description", ""),
            }
        )
    return final_plan


def generate_rearrangement_plan(
    backbone_groups: List[BackboneGroup],
    matches: OrphanMatchResponse,
    *,
    llm_gateway: Optional[LLMGateway] = None,
) -> List[Dict]:
    """Combine backbone groups and orphan matches into a final rearrangement plan."""
    plan_map = _init_plan_from_backbone(backbone_groups)
    _distribute_matches(plan_map, matches)

    save_debug_log(_serialize_plan(plan_map), "06_pre_refinement_plan")

    gateway = llm_gateway or LLMGateway()
    _refine_misc_group(plan_map, gateway)

    final_plan = _serialize_plan(plan_map)
    print(f"Generated rearrangement plan with {len(final_plan)} groups.")
    return final_plan


def merge_matches_into_groups(
    groups: List[BackboneGroup],
    matches: OrphanMatchResponse,
    *,
    llm_gateway: Optional[LLMGateway] = None,
) -> List[RearrangedGroup]:
    """Merge orphan matches into backbone groups, producing RearrangedGroups."""
    plan_dicts = generate_rearrangement_plan(groups, matches, llm_gateway=llm_gateway)
    return [
        RearrangedGroup(
            group_name=p["group_name"],
            main_item=p["main_item"],
            related_items=p["related_items"],
        )
        for p in plan_dicts
    ]
