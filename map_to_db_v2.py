"""
Course reorganization orchestrator.

This v2 keeps one entry point for multiple courses, but intentionally does not
flatten the course-specific matching logic into one generic matcher. CS61A and
EECS106B have different row-cardinality rules, UUID policies, fallback behavior,
and path matchers, so this script delegates to the proven course implementations
and adds shared safety features around them.

Usage:
    python map_to_db_v2.py --course cs61a --dry-run
    python map_to_db_v2.py --course cs61a --output-table file_new
    python map_to_db_v2.py --course eecs106b --dry-run
    python map_to_db_v2.py --course eecs106b --output-table file_new
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import sqlite3
from collections import Counter
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


class CS61ARunner:
    def __init__(self, output_table: str, create_logical_output: bool) -> None:
        self.module = load_module(
            "cs61a_reorganization_v3",
            os.path.join(REPO_DIR, "61A", "reorganization_v3.py"),
        )
        self.module.NEW_TABLE = output_table
        self.module.CREATE_LOGICAL_OUTPUT = create_logical_output

    def dry_run(self) -> None:
        m = self.module
        print("CS61A dry run")
        print(f"DB_PATH: {m.DB_PATH}")
        print(f"JSON_PATH: {m.JSON_PATH}")
        print(f"REORGANIZED_GROUNDTRUTH_ROOT: {m.REORGANIZED_GROUNDTRUTH_ROOT}")
        print(f"Output table would be: {m.NEW_TABLE}")
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


def build_runner(course: str, output_table: str, create_logical_output: bool):
    normalized = course.lower().replace("_", "").replace("-", "")

    if normalized in {"cs61a", "61a"}:
        return CS61ARunner(
            output_table=output_table,
            create_logical_output=create_logical_output,
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
        help="For CS61A, also create the physical/symlink logical output tree.",
    )
    args = parser.parse_args()

    runner = build_runner(
        course=args.course,
        output_table=args.output_table,
        create_logical_output=args.create_logical_output,
    )

    if args.dry_run:
        runner.dry_run()
    else:
        runner.run(cleanup=args.cleanup)


if __name__ == "__main__":
    main()
