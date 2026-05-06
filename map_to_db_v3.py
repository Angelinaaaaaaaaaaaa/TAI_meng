"""
Course reorganization orchestrator.

This v3 keeps one entry point for multiple courses, but intentionally does not
flatten the course-specific matching logic into one generic matcher. CS61A and
EECS106B have different row-cardinality rules, UUID policies, fallback behavior,
and path matchers, so this script delegates to the proven course implementations
and adds shared safety features around them.

For logical output, v3 uses the same storage policy as reorganization_v3:
the first JSON occurrence is stored physically, and later occurrences are
symlinks. The difference is that v3 applies this at the JSON item level. If the
last path component has no file extension, it is treated as a folder-level item.

Usage:
    python map_to_db_v3.py --course cs61a --dry-run
    python map_to_db_v3.py --course cs61a --output-table file_new
    python map_to_db_v3.py --course cs61a --create-logical-output
    python map_to_db_v3.py --course eecs106b --dry-run
    python map_to_db_v3.py --course eecs106b --output-table file_new
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sqlite3
import hashlib
from collections import Counter, defaultdict
from datetime import datetime
from types import ModuleType
from typing import Iterable


REPO_DIR = os.path.dirname(os.path.abspath(__file__))


def load_module(name: str, path: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module from {path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def backup_database(db_path: str, label: str) -> str:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found: {db_path}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    root, ext = os.path.splitext(db_path)
    backup_path = f"{root}.backup_before_{label}_{ts}{ext}"
    shutil.copy2(db_path, backup_path)
    print(f"Backup created: {backup_path}")
    return backup_path


def cleanup_database(conn: sqlite3.Connection, keep_tables: Iterable[str]) -> None:
    keep = set(keep_tables)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()

    for (table_name,) in rows:
        if table_name in keep:
            continue
        print(f"  Dropping table: {table_name}")
        quoted = '"' + table_name.replace('"', '""') + '"'
        conn.execute(f"DROP TABLE IF EXISTS {quoted}")

    conn.commit()


def print_path_duplicate_summary(rows: list[dict], path_key: str, limit: int = 12) -> None:
    counts = Counter(row.get(path_key) for row in rows if row.get(path_key))
    duplicates = [(path, count) for path, count in counts.items() if count > 1]
    duplicates.sort(key=lambda item: (-item[1], item[0]))

    print(f"Duplicate {path_key} values: {len(duplicates)}")
    for path, count in duplicates[:limit]:
        print(f"  {count:>4}  {path}")


def normalize_slash_path(path: str) -> str:
    return str(path or "").replace("\\", "/").strip("/")


def is_file_like_string(path: str) -> bool:
    base = os.path.basename(normalize_slash_path(path))
    return bool(os.path.splitext(base)[1])


def safe_relative_output_path(path: str) -> str:
    normalized = normalize_slash_path(path)
    if not normalized:
        raise ValueError("Output path cannot be empty.")

    parts = [part for part in normalized.split("/") if part]
    if any(part == ".." for part in parts):
        raise ValueError(f"Refusing unsafe output path: {path}")

    return "/".join(parts)


def is_inside_directory(path: str, directory: str) -> bool:
    path_abs = os.path.abspath(path)
    directory_abs = os.path.abspath(directory)
    return os.path.commonpath([path_abs, directory_abs]) == directory_abs


def has_symlink_parent(path: str, root_dir: str) -> bool:
    root_abs = os.path.abspath(root_dir)
    current = os.path.abspath(os.path.dirname(path))

    while is_inside_directory(current, root_abs) and current != root_abs:
        if os.path.islink(current):
            return True
        current = os.path.dirname(current)

    return False


def remove_existing_output_path(path: str) -> None:
    if os.path.islink(path):
        os.unlink(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)
    elif os.path.lexists(path):
        os.remove(path)


def copy_physical_item(src_abs: str, dst_abs: str, root_dir: str) -> bool:
    if not is_inside_directory(dst_abs, root_dir):
        raise ValueError(f"Refusing to write copy outside logical root: {dst_abs}")

    if has_symlink_parent(dst_abs, root_dir):
        return False

    os.makedirs(os.path.dirname(dst_abs), exist_ok=True)
    remove_existing_output_path(dst_abs)

    if os.path.isdir(src_abs):
        shutil.copytree(src_abs, dst_abs, symlinks=True)
    else:
        shutil.copy2(src_abs, dst_abs)

    return True


def replace_with_symlink(
    src_abs: str,
    dst_abs: str,
    root_dir: str,
    target_is_directory: bool,
) -> bool:
    if os.path.abspath(src_abs) == os.path.abspath(dst_abs):
        return False

    if not is_inside_directory(dst_abs, root_dir):
        raise ValueError(f"Refusing to write symlink outside logical root: {dst_abs}")

    if has_symlink_parent(dst_abs, root_dir):
        return False

    os.makedirs(os.path.dirname(dst_abs), exist_ok=True)
    remove_existing_output_path(dst_abs)

    rel_target = os.path.relpath(src_abs, os.path.dirname(dst_abs))
    try:
        os.symlink(rel_target, dst_abs, target_is_directory=target_is_directory)
    except OSError:
        os.symlink(src_abs, dst_abs, target_is_directory=target_is_directory)

    return True


def module_normalize_path(module: ModuleType, path: str) -> str:
    normalizer = getattr(module, "normalize_relative_path", None)
    if normalizer is not None:
        return normalizer(path)

    return str(path or "").replace("\\", "/").strip("/")


def path_parts(module: ModuleType, path: str) -> list[str]:
    return [part for part in module_normalize_path(module, path).split("/") if part]


def strip_first_path_part(module: ModuleType, path: str) -> str:
    parts = path_parts(module, path)
    if len(parts) <= 1:
        return "/".join(parts)
    return "/".join(parts[1:])


def is_file_like_plan_path(module: ModuleType, path: str) -> bool:
    detector = getattr(module, "is_file_like_path", None)
    if detector is not None:
        return detector(path)

    base = os.path.basename(module_normalize_path(module, path))
    return bool(os.path.splitext(base)[1])


def dedupe_items(items: Iterable[str]) -> list[str]:
    seen = set()
    out = []

    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)

    return out


def common_parent_path(paths: list[str]) -> str | None:
    parents = [normalize_slash_path(os.path.dirname(path)) for path in paths if path]
    if not parents:
        return None

    split_parents = [parent.split("/") for parent in parents]
    common = []

    for components in zip(*split_parents):
        if len(set(components)) != 1:
            break
        common.append(components[0])

    return "/".join(part for part in common if part)


def collect_descendant_final_paths(node: dict) -> list[str]:
    paths = []

    if node.get("type") == "file" and node.get("final_path"):
        paths.append(safe_relative_output_path(node["final_path"]))

    for child in node.get("children") or []:
        paths.extend(collect_descendant_final_paths(child))

    return paths


def load_tree_json_records(json_path: str) -> tuple[list[dict], dict[str, list[str]]]:
    with open(json_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    if not isinstance(data, list):
        raise ValueError("Expected tree JSON top level to be a list.")

    file_records = []
    folder_groups = defaultdict(list)

    def walk(nodes):
        for node in nodes:
            node_type = node.get("type")

            if node_type == "file":
                source = normalize_slash_path(node.get("source"))
                raw_final_path = normalize_slash_path(node.get("final_path"))
                if source and raw_final_path:
                    final_path = safe_relative_output_path(raw_final_path)
                    file_records.append(
                        {
                            "source": source,
                            "final_path": final_path,
                            "file_hash": node.get("file_hash"),
                            "file_name": node.get("name") or os.path.basename(final_path),
                            "description": node.get("description"),
                        }
                    )

            if node_type == "folder":
                relative_path = normalize_slash_path(node.get("relative_path"))
                if relative_path and not is_file_like_string(relative_path):
                    final_paths = collect_descendant_final_paths(node)
                    output_folder = common_parent_path(final_paths)
                    if output_folder:
                        folder_groups[relative_path].append(output_folder)

            walk(node.get("children") or [])

    walk(data)

    deduped_folder_groups = {
        key: dedupe_items(paths)
        for key, paths in folder_groups.items()
        if len(dedupe_items(paths)) > 1
    }

    return file_records, deduped_folder_groups


def dedupe_file_records_by_final_path(file_records: list[dict]) -> tuple[list[dict], int]:
    seen = set()
    deduped = []
    skipped = 0

    for record in file_records:
        final_path = record["final_path"]
        if final_path in seen:
            skipped += 1
            continue
        seen.add(final_path)
        deduped.append(record)

    return deduped, skipped


def resolve_tree_source(source_root: str, source: str) -> str:
    source_abs = os.path.abspath(os.path.join(source_root, normalize_slash_path(source)))
    if not is_inside_directory(source_abs, source_root):
        raise ValueError(f"Refusing source outside source root: {source}")
    return source_abs


def sha256_path(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def build_source_basename_index(source_root: str) -> dict[str, list[str]]:
    index = defaultdict(list)

    for root, _, files in os.walk(source_root):
        for file_name in files:
            if file_name in {".DS_Store", "Thumbs.db", "desktop.ini"}:
                continue
            if file_name.startswith("._"):
                continue
            path = os.path.join(root, file_name)
            index[file_name.lower()].append(path)

    return index


def iter_source_files(source_root: str):
    for root, _, files in os.walk(source_root):
        for file_name in files:
            if file_name in {".DS_Store", "Thumbs.db", "desktop.ini"}:
                continue
            if file_name.startswith("._"):
                continue
            yield os.path.join(root, file_name)


def build_source_hash_index(source_root: str) -> dict[str, str]:
    index = {}

    for path in iter_source_files(source_root):
        try:
            index.setdefault(sha256_path(path), path)
        except OSError:
            continue

    return index


def resolve_tree_record_source(
    source_root: str,
    record: dict,
    basename_index: dict[str, list[str]],
    hash_index: dict[str, str] | None = None,
) -> str | None:
    source_abs = resolve_tree_source(source_root, record["source"])
    if os.path.isfile(source_abs):
        return source_abs

    basename = os.path.basename(normalize_slash_path(record["source"])).lower()
    basename_matches = basename_index.get(basename, [])

    if len(basename_matches) == 1:
        return basename_matches[0]

    expected_hash = record.get("file_hash")
    if expected_hash:
        for candidate in basename_matches:
            try:
                if sha256_path(candidate) == expected_hash:
                    return candidate
            except OSError:
                continue

        if hash_index is not None and expected_hash in hash_index:
            return hash_index[expected_hash]

    return None


def materialize_tree_records(
    file_records: list[dict],
    folder_groups: dict[str, list[str]],
    source_root: str,
    output_root: str,
) -> dict:
    os.makedirs(output_root, exist_ok=True)

    grouped_files = defaultdict(list)
    for record in file_records:
        grouped_files[record["source"]].append(record)

    copied_files = 0
    linked_files = 0
    linked_folders = 0
    missing_sources = []
    basename_index = build_source_basename_index(source_root)
    hash_index = None

    for source, records in grouped_files.items():
        source_abs = resolve_tree_record_source(source_root, records[0], basename_index)
        if source_abs is None and records[0].get("file_hash"):
            if hash_index is None:
                hash_index = build_source_hash_index(source_root)
            source_abs = resolve_tree_record_source(
                source_root,
                records[0],
                basename_index,
                hash_index=hash_index,
            )

        if source_abs is None:
            missing_sources.append(source)
            continue

        output_paths = dedupe_items([record["final_path"] for record in records])
        canonical_abs = os.path.abspath(os.path.join(output_root, output_paths[0]))

        if copy_physical_item(source_abs, canonical_abs, output_root):
            copied_files += 1

        for output_path in output_paths[1:]:
            dst_abs = os.path.abspath(os.path.join(output_root, output_path))
            if replace_with_symlink(
                src_abs=canonical_abs,
                dst_abs=dst_abs,
                root_dir=output_root,
                target_is_directory=False,
            ):
                linked_files += 1

    for _, output_folders in folder_groups.items():
        canonical_abs = os.path.abspath(os.path.join(output_root, output_folders[0]))
        if not os.path.isdir(canonical_abs):
            continue

        for output_folder in output_folders[1:]:
            dst_abs = os.path.abspath(os.path.join(output_root, output_folder))
            if replace_with_symlink(
                src_abs=canonical_abs,
                dst_abs=dst_abs,
                root_dir=output_root,
                target_is_directory=True,
            ):
                linked_folders += 1

    return {
        "file_records": len(file_records),
        "unique_sources": len(grouped_files),
        "copied_files": copied_files,
        "file_symlinks": linked_files,
        "folder_symlinks": linked_folders,
        "missing_sources": missing_sources,
        "folder_groups": len(folder_groups),
    }


def write_tree_metadata_table(
    input_db_path: str,
    output_db_path: str,
    file_records: list[dict],
    output_table: str,
) -> None:
    if os.path.abspath(input_db_path) != os.path.abspath(output_db_path):
        os.makedirs(os.path.dirname(output_db_path), exist_ok=True)
        shutil.copy2(input_db_path, output_db_path)

    conn = sqlite3.connect(output_db_path)
    try:
        cur = conn.cursor()
        quoted_table = '"' + output_table.replace('"', '""') + '"'
        cur.execute(f"DROP TABLE IF EXISTS {quoted_table}")
        cur.execute(
            f"""
            CREATE TABLE {quoted_table} (
              uuid          TEXT PRIMARY KEY,
              file_hash     TEXT,
              relative_path TEXT,
              file_name     TEXT,
              logical_path  TEXT,
              source_path   TEXT,
              description   TEXT
            )
            """
        )

        for index, record in enumerate(file_records, start=1):
            cur.execute(
                f"""
                INSERT INTO {quoted_table} (
                  uuid, file_hash, relative_path, file_name,
                  logical_path, source_path, description
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"tree-{index:06d}",
                    record.get("file_hash"),
                    record.get("source"),
                    record.get("file_name"),
                    record.get("final_path"),
                    record.get("source"),
                    record.get("description"),
                ),
            )

        cur.execute(
            f"CREATE UNIQUE INDEX idx_{output_table}_logical_path ON {quoted_table}(logical_path)"
        )
        cur.execute(
            f"CREATE INDEX idx_{output_table}_source_path ON {quoted_table}(source_path)"
        )
        conn.commit()
    finally:
        conn.close()


def run_tree_json_mode(
    module: ModuleType,
    tree_json: str,
    source_root: str,
    output_root: str,
    output_db: str,
    output_table: str,
) -> None:
    source_root_abs = os.path.abspath(source_root)
    output_root_abs = os.path.abspath(output_root)

    if not os.path.isdir(source_root_abs):
        raise FileNotFoundError(f"Source dataset folder not found: {source_root_abs}")

    if is_inside_directory(output_root_abs, source_root_abs):
        raise ValueError("Output folder cannot be inside the source dataset folder.")

    file_records, folder_groups = load_tree_json_records(tree_json)
    file_records, skipped_duplicate_outputs = dedupe_file_records_by_final_path(
        file_records
    )
    write_tree_metadata_table(
        input_db_path=module.DB_PATH,
        output_db_path=output_db,
        file_records=file_records,
        output_table=output_table,
    )
    summary = materialize_tree_records(
        file_records=file_records,
        folder_groups=folder_groups,
        source_root=source_root_abs,
        output_root=output_root_abs,
    )

    print("Tree JSON run complete.")
    print(f"Output DB: {output_db}")
    print(f"Output folder: {output_root_abs}")
    print(f"duplicate_final_paths_skipped: {skipped_duplicate_outputs}")
    for key, value in summary.items():
        if key == "missing_sources":
            print(f"missing_sources: {len(value)}")
            for item in value[:12]:
                print(f"  MISSING: {item}")
        else:
            print(f"{key}: {value}")


def plan_item_key(module: ModuleType, plan_item: str) -> str:
    stripped = strip_first_path_part(module, plan_item)
    return module_normalize_path(module, stripped or plan_item)


def candidate_output_paths(module: ModuleType, plan_item: str, group: dict) -> list[str]:
    is_folder = not is_file_like_plan_path(module, plan_item)
    candidate_builder = getattr(module, "candidate_prefixes_for_plan_item", None)
    lecture_extractor = getattr(module, "extract_lecture_number_from_group_name", None)

    if candidate_builder is not None and lecture_extractor is not None:
        group_context = lecture_extractor(group.get("group_name", ""))
        if group_context is not None:
            candidates = candidate_builder(plan_item, group_context)
            same_level = [
                c["output_prefix"]
                for c in candidates
                if is_file_like_plan_path(module, c["output_prefix"]) != is_folder
            ]
            if same_level:
                return [module_normalize_path(module, same_level[0])]

    return [plan_item_key(module, plan_item)]


def source_path_candidates(module: ModuleType, plan_key: str) -> list[str]:
    source_rel = module_normalize_path(module, plan_key)
    candidates = []
    variant_builder = getattr(module, "build_path_variants", None)

    variants = variant_builder(source_rel) if variant_builder is not None else [source_rel]
    for variant in variants:
        candidates.append(os.path.abspath(os.path.join(module.ORIGINAL_ROOT, variant)))

    seen = set()
    return [p for p in candidates if not (p in seen or seen.add(p))]


def first_existing_source_path(
    module: ModuleType,
    plan_key: str,
    is_folder: bool,
) -> str | None:
    for candidate in source_path_candidates(module, plan_key):
        if is_folder and os.path.isdir(candidate):
            return candidate
        if not is_folder and os.path.isfile(candidate):
            return candidate
    return None


def build_json_plan_item_groups(module: ModuleType) -> dict[str, dict]:
    if not os.path.exists(module.JSON_PATH):
        return {}

    with open(module.JSON_PATH, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    if not isinstance(data, list):
        raise ValueError("Expected top-level JSON plan to be a list.")

    grouped = defaultdict(list)
    folder_flags = {}

    for group in data:
        plan_items = []
        main_item = group.get("main_item", "")
        related_items = group.get("related_items", []) or []
        if main_item:
            plan_items.append(main_item)
        plan_items.extend(related_items)

        for raw_item in dedupe_items(plan_items):
            plan_item = module_normalize_path(module, raw_item)
            if not plan_item:
                continue

            is_folder = not is_file_like_plan_path(module, plan_item)
            output_paths = candidate_output_paths(module, plan_item, group)
            if not output_paths:
                continue

            plan_key = plan_item_key(module, plan_item)
            grouped[plan_key].extend(output_paths)
            folder_flags[plan_key] = is_folder

    item_groups = {}
    for plan_key, output_paths in grouped.items():
        item_groups[plan_key] = {
            "is_folder": folder_flags[plan_key],
            "output_paths": dedupe_items(
                [module_normalize_path(module, p) for p in output_paths]
            ),
        }

    return item_groups


def materialize_json_plan_item_tree(module: ModuleType) -> None:
    item_groups = build_json_plan_item_groups(module)

    if getattr(module, "CLEAR_LOGICAL_ROOT_FIRST", False):
        print(f"Clearing logical root: {module.LOGICAL_ROOT}")
        safe_remove = getattr(module, "safe_remove_tree_contents", None)
        if safe_remove is None:
            shutil.rmtree(module.LOGICAL_ROOT, ignore_errors=True)
        else:
            safe_remove(module.LOGICAL_ROOT)

    os.makedirs(module.LOGICAL_ROOT, exist_ok=True)

    copied = 0
    linked = 0
    missing_sources = []

    for plan_key, info in item_groups.items():
        output_paths = info["output_paths"]
        is_folder = info["is_folder"]

        if not output_paths:
            continue

        source_abs = first_existing_source_path(module, plan_key, is_folder=is_folder)
        if source_abs is None:
            missing_sources.append(plan_key)
            continue

        canonical_abs = os.path.abspath(os.path.join(module.LOGICAL_ROOT, output_paths[0]))
        if copy_physical_item(
            src_abs=source_abs,
            dst_abs=canonical_abs,
            root_dir=module.LOGICAL_ROOT,
        ):
            copied += 1

        for output_path in output_paths[1:]:
            dst_abs = os.path.abspath(os.path.join(module.LOGICAL_ROOT, output_path))
            if replace_with_symlink(
                src_abs=canonical_abs,
                dst_abs=dst_abs,
                root_dir=module.LOGICAL_ROOT,
                target_is_directory=is_folder,
            ):
                linked += 1

    print(
        "JSON item logical tree complete. "
        f"items={len(item_groups)}, physical_copies={copied}, symlinks={linked}"
    )

    if missing_sources:
        print(f"JSON item sources missing: {len(missing_sources)}")
        for plan_key in missing_sources[:12]:
            print(f"  MISSING: {plan_key}")


class CS61ARunner:
    def __init__(
        self,
        output_table: str,
        create_logical_output: bool,
        create_folder_symlinks: bool,
    ) -> None:
        self.module = load_module(
            "cs61a_reorganization_v3",
            os.path.join(REPO_DIR, "61A", "reorganization_v3.py"),
        )
        self.module.NEW_TABLE = output_table
        self.module.CREATE_LOGICAL_OUTPUT = False
        self.requested_logical_output = create_logical_output
        self.create_folder_symlinks = create_folder_symlinks

    def dry_run(self) -> None:
        m = self.module
        print("CS61A dry run")
        print(f"DB_PATH: {m.DB_PATH}")
        print(f"JSON_PATH: {m.JSON_PATH}")
        print(f"REORGANIZED_GROUNDTRUTH_ROOT: {m.REORGANIZED_GROUNDTRUTH_ROOT}")
        print(f"Output table would be: {m.NEW_TABLE}")
        print(f"Create JSON item logical output: {self.requested_logical_output}")
        print(f"Use symlinks for repeated JSON items: {self.create_folder_symlinks}")
        print()

        conn = sqlite3.connect(f"file:{m.DB_PATH}?mode=ro", uri=True)
        try:
            if not m.table_exists(conn, m.OLD_TABLE):
                raise ValueError(f"Table does not exist: {m.OLD_TABLE}")

            columns, raw_rows = m.fetch_old_file_rows(conn)
            old_rows = [m.row_to_dict(columns, row) for row in raw_rows]
        finally:
            conn.close()

        print(f"Old rows: {len(old_rows)}")

        old_indexes = m.build_old_row_indexes(old_rows)
        disk_hash_index = m.build_disk_hash_index_for_old_rows(old_rows)

        study_updates = m.build_updates_from_json_plan(old_rows)
        practice_updates = m.scan_reorganized_subtree(
            "practice", old_indexes, disk_hash_index
        )
        support_updates = m.scan_reorganized_subtree(
            "support", old_indexes, disk_hash_index
        )

        aggregated = m.merge_aggregated_maps(
            study_updates,
            practice_updates,
            support_updates,
        )
        print(f"Matched source files before duplicate resolution: {len(aggregated)}")

        aggregated = m.resolve_duplicate_logical_paths(aggregated)
        m.validate_no_duplicate_logical_paths(aggregated)
        expanded_rows = m.build_expanded_rows(old_rows, aggregated)

        print(f"Expanded output rows: {len(expanded_rows)}")
        print(f"Touched source rows: {len(aggregated)}")
        print_path_duplicate_summary(expanded_rows, "logical_path")
        original_fallbacks = sum(
            1
            for row in expanded_rows
            if m.normalize_relative_path(row.get("logical_path")).startswith("original/")
        )
        print(f"original/... fallback rows: {original_fallbacks}")
        item_groups = build_json_plan_item_groups(m)
        repeated_groups = [
            paths
            for paths in (info["output_paths"] for info in item_groups.values())
            if len(paths) > 1
        ]
        link_targets = sum(len(paths) - 1 for paths in repeated_groups)
        folder_groups = sum(1 for info in item_groups.values() if info["is_folder"])
        print(f"JSON item groups: {len(item_groups)}")
        print(f"JSON folder-level groups: {folder_groups}")
        print(f"Repeated JSON item groups: {len(repeated_groups)}")
        print(f"Repeated JSON symlink targets: {link_targets}")

    def run(self, cleanup: bool) -> None:
        m = self.module
        backup_database(m.DB_PATH, m.NEW_TABLE)
        m.main()

        if self.requested_logical_output:
            if self.create_folder_symlinks:
                materialize_json_plan_item_tree(m)
            else:
                print("JSON item logical output requested, but symlink pass is disabled.")

        if cleanup:
            conn = sqlite3.connect(m.DB_PATH)
            try:
                cleanup_database(
                    conn,
                    keep_tables={m.OLD_TABLE, "problem", "chunks", m.NEW_TABLE},
                )
            finally:
                conn.close()


class EECS106BRunner:
    def __init__(self, output_table: str) -> None:
        self.module = load_module(
            "eecs106b_reorganization",
            os.path.join(REPO_DIR, "106B", "reorganization.py"),
        )
        self.module.NEW_TABLE = output_table

    def dry_run(self) -> None:
        m = self.module
        print("EECS106B dry run")
        print(f"DB_PATH: {m.DB_PATH}")
        print(f"JSON_PATH: {m.JSON_PATH}")
        print(f"PRACTICE_SUPPORT_GT_ROOT: {m.PRACTICE_SUPPORT_GT_ROOT}")
        print(f"Output table would be: {m.NEW_TABLE}")
        print()

        json_records = m.extract_json_target_records(m.JSON_PATH)
        gt_records = m.scan_practice_support_gt(m.PRACTICE_SUPPORT_GT_ROOT)

        conn = sqlite3.connect(f"file:{m.DB_PATH}?mode=ro", uri=True)
        try:
            if not m.table_exists(conn, m.OLD_TABLE):
                raise ValueError(f"Table does not exist: {m.OLD_TABLE}")

            old_columns, old_rows = m.fetch_rows_as_dicts(conn, m.OLD_TABLE)
        finally:
            conn.close()

        output_rows, report_rows = m.build_output_rows(
            old_rows=old_rows,
            json_records=json_records,
            gt_records=gt_records,
        )

        source_counts = Counter(row.get("source_type") for row in report_rows)
        reason_counts = Counter(row.get("reason") for row in report_rows)

        print(f"Old rows: {len(old_rows)}")
        print(f"Output rows: {len(output_rows)}")
        print("Source types:")
        for name, count in source_counts.most_common():
            print(f"  {name}: {count}")
        print("Top match reasons:")
        for name, count in reason_counts.most_common(12):
            print(f"  {name}: {count}")

        path_col = "file_path" if m.DEVELOP_MODE == "down" else "logical_path"
        logical_key = "logical_path"
        print_path_duplicate_summary(output_rows, logical_key)
        original_fallbacks = sum(
            1
            for row in output_rows
            if m.normalize_relative_path(row.get(logical_key)).startswith("original/")
        )
        print(f"original/... fallback rows: {original_fallbacks}")
        print(f"DB path column after schema projection: {path_col}")

    def run(self, cleanup: bool) -> None:
        m = self.module
        backup_database(m.DB_PATH, m.NEW_TABLE)
        m.main()

        if cleanup:
            conn = sqlite3.connect(m.DB_PATH)
            try:
                cleanup_database(
                    conn,
                    keep_tables={m.OLD_TABLE, "problem", "chunks", m.NEW_TABLE},
                )
            finally:
                conn.close()


def build_runner(
    course: str,
    output_table: str,
    create_logical_output: bool,
    create_folder_symlinks: bool,
):
    normalized = course.lower().replace("_", "").replace("-", "")

    if normalized in {"cs61a", "61a"}:
        return CS61ARunner(
            output_table=output_table,
            create_logical_output=create_logical_output,
            create_folder_symlinks=create_folder_symlinks,
        )

    if normalized in {"eecs106b", "106b"}:
        return EECS106BRunner(output_table=output_table)

    raise ValueError(f"Unknown course: {course}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Map course materials to a reorganized DB table."
    )
    parser.add_argument(
        "--course",
        required=True,
        help="Course name: cs61a or eecs106b",
    )
    parser.add_argument(
        "--output-table",
        default="file_new",
        help="Output table to create. Defaults to file_new.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build mappings in memory and print accuracy stats without writing the DB.",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="After a successful write, drop all tables except file/problem/chunks/output.",
    )
    parser.add_argument(
        "--create-logical-output",
        action="store_true",
        help="Also create the physical/symlink logical output tree.",
    )
    parser.add_argument(
        "--no-folder-symlinks",
        action="store_true",
        help="Skip v3 folder-level directory symlinks in logical output.",
    )
    parser.add_argument(
        "--tree-json",
        help=(
            "Run directly from a rearrangement tree JSON containing file nodes "
            "with source/final_path fields."
        ),
    )
    parser.add_argument(
        "--source-root",
        help="Original unstructured dataset root to read from in --tree-json mode.",
    )
    parser.add_argument(
        "--logical-root",
        help="Output folder to create in --tree-json mode.",
    )
    parser.add_argument(
        "--db-output",
        help="Copied output metadata DB path in --tree-json mode.",
    )
    parser.add_argument(
        "--tree-output-table",
        default="file_new_tree",
        help="Output metadata table for --tree-json mode. Defaults to file_new_tree.",
    )
    args = parser.parse_args()

    runner = build_runner(
        course=args.course,
        output_table=args.output_table,
        create_logical_output=args.create_logical_output,
        create_folder_symlinks=not args.no_folder_symlinks,
    )

    if args.tree_json:
        missing = [
            name
            for name, value in {
                "--source-root": args.source_root,
                "--logical-root": args.logical_root,
                "--db-output": args.db_output,
            }.items()
            if not value
        ]
        if missing:
            raise ValueError(
                "--tree-json mode requires " + ", ".join(missing)
            )

        run_tree_json_mode(
            module=runner.module,
            tree_json=args.tree_json,
            source_root=args.source_root,
            output_root=args.logical_root,
            output_db=args.db_output,
            output_table=args.tree_output_table,
        )
        return

    if args.dry_run:
        runner.dry_run()
    else:
        runner.run(cleanup=args.cleanup)


if __name__ == "__main__":
    main()
