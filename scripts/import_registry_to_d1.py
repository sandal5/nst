#!/usr/bin/env python3
"""Generate D1 (SQLite) import SQL for the nst registry CSV assets.

Reads the registry CSVs in data/ and writes one .sql file containing
batched INSERT statements (at most --batch-size rows per transaction,
default 500) for the tables defined in worker/schema_registry.sql.

Usage:
    python3 scripts/import_registry_to_d1.py [--data-dir DIR] [--out FILE]
                                             [--only table,table,...]
                                             [--batch-size N]

Mapping rules (column mapping follows the CSV headers exactly):
  * CSVs are read as UTF-8 with BOM tolerance (utf-8-sig).
  * Empty CSV fields become NULL.
  * INTEGER columns are parsed as integers; a non-empty non-integer value
    aborts the run.
  * Boolean flag columns (in_kkj, in_dir2_top) store INTEGER 0/1:
    'yes' -> 1, 'no' -> 0, '' -> NULL. A qualified value such as
    'no (...)' maps to 0 and, for in_kkj, the original text is preserved
    in the companion column in_kkj_raw (NULL for plain yes/no/empty).
  * Each table's rows are preceded by DELETE FROM <table>; so the generated
    SQL can be re-applied to refresh a registry snapshot.
  * A CSV file that is missing from --data-dir is skipped with a warning
    on stderr (and a comment in the output), so partial data dirs still
    produce a usable import for the tables they do have.
"""

import argparse
import csv
import os
import sys

# kind: "text" | "int" | "bool"
# Each entry: (csv_column, db_column, kind). Order matches the table DDL.
TABLES = {
    "master_source_list": {
        "csv": "master_source_list.csv",
        "columns": [
            ("prefecture", "prefecture", "text"),
            ("level", "level", "text"),
            ("org_name", "org_name", "text"),
            ("group_ministry", "group_ministry", "text"),
            ("source_url_kkj", "source_url_kkj", "text"),
            ("official_domain", "official_domain", "text"),
            ("framework_guess_by_url", "framework_guess_by_url", "text"),
            ("in_kkj", "in_kkj", "bool"),
            ("in_dir2_top", "in_dir2_top", "bool"),
            ("verification_status", "verification_status", "text"),
            ("daily_collection_target", "daily_collection_target", "text"),
            ("source_note", "source_note", "text"),
        ],
        # db columns that are not direct CSV columns
        "extra_columns": ["in_kkj_raw"],
    },
    "objects_registry": {
        "csv": "objects_registry.csv",
        "columns": [
            ("object_id", "object_id", "text"),
            ("org_name", "org_name", "text"),
            ("prefecture", "prefecture", "text"),
            ("level", "level", "text"),
            ("official_domain", "official_domain", "text"),
            ("candidate_endpoint_url", "candidate_endpoint_url", "text"),
            ("framework", "framework", "text"),
            ("source", "source", "text"),
        ],
    },
    "endpoints": {
        "csv": "endpoints.csv",
        "columns": [
            ("endpoint_id", "endpoint_id", "text"),
            ("url", "url", "text"),
            ("host", "host", "text"),
            ("object_count", "object_count", "int"),
            ("object_ids", "object_ids", "text"),
            ("poll_tier", "poll_tier", "text"),
            ("terms_status", "terms_status", "text"),
            ("status", "status", "text"),
        ],
    },
    "object_lines": {
        "csv": "object_lines.csv",
        "columns": [
            ("object_id", "object_id", "text"),
            ("org_name", "org_name", "text"),
            ("line_label", "line_label", "text"),
            ("line_url", "line_url", "text"),
            ("note", "note", "text"),
        ],
    },
    "site_templates": {
        "csv": "site_templates.csv",
        "columns": [
            ("org_name", "org_name", "text"),
            ("domain", "domain", "text"),
            ("list_url", "list_url", "text"),
            ("p_class", "p_class", "text"),
            ("list_structure", "list_structure", "text"),
            ("detail_pattern", "detail_pattern", "text"),
            ("pagination", "pagination", "text"),
            ("detail_format", "detail_format", "text"),
            ("evidence", "evidence", "text"),
        ],
    },
    "dir2_objects": {
        "csv": "dir2_objects.csv",
        "columns": [
            ("org_name", "org_name", "text"),
            ("prefecture", "prefecture", "text"),
            ("official_url", "official_url", "text"),
            ("source_page", "source_page", "text"),
        ],
    },
    "open_items": {
        "csv": "open_items.csv",
        "columns": [
            ("org_name", "org_name", "text"),
            ("prefecture", "prefecture", "text"),
            ("level", "level", "text"),
            ("official_domain", "official_domain", "text"),
            ("source_url", "source_url", "text"),
            ("bucket", "bucket", "text"),
            ("status", "status", "text"),
            ("next_action", "next_action", "text"),
        ],
    },
    "candidates_joint_bodies": {
        "csv": "candidates_joint_bodies.csv",
        "columns": [
            ("org_name", "org_name", "text"),
            ("prefecture", "prefecture", "text"),
            ("code", "code", "text"),
            ("official_url_from_list", "official_url_from_list", "text"),
            ("source_list", "source_list", "text"),
            ("note", "note", "text"),
            ("verification_status", "verification_status", "text"),
        ],
    },
}


def sql_literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, int):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def convert(raw, kind, table, csv_col, row_no):
    """Convert one CSV field to a Python value ready for sql_literal."""
    if raw is None:
        raw = ""
    if kind == "text":
        return raw if raw != "" else None
    if kind == "int":
        if raw == "":
            return None
        try:
            return int(raw)
        except ValueError:
            raise SystemExit(
                f"{table}.{csv_col} row {row_no}: not an integer: {raw!r}"
            )
    if kind == "bool":
        if raw == "":
            return None
        low = raw.strip().lower()
        if low == "yes":
            return 1
        if low == "no" or low.startswith("no "):
            return 0
        raise SystemExit(f"{table}.{csv_col} row {row_no}: not a yes/no flag: {raw!r}")
    raise AssertionError(kind)


def row_values(row, spec, table, row_no):
    values = []
    raw_by_col = {}
    for csv_col, db_col, kind in spec["columns"]:
        raw = row.get(csv_col)
        raw_by_col[csv_col] = raw if raw is not None else ""
        values.append(convert(raw, kind, table, csv_col, row_no))
    # Companion raw-preservation column for master_source_list.in_kkj:
    # keep the original text only when it is a qualified value (not plain
    # '', 'yes' or 'no'), so no information from the CSV is lost.
    if "in_kkj_raw" in spec.get("extra_columns", []):
        raw = raw_by_col.get("in_kkj", "")
        values.append(raw if raw not in ("", "yes", "no") else None)
    return values


def db_columns(spec):
    cols = [db_col for _csv, db_col, _kind in spec["columns"]]
    cols.extend(spec.get("extra_columns", []))
    return cols


def emit_table(out, table, spec, data_dir, batch_size):
    path = os.path.join(data_dir, spec["csv"])
    if not os.path.exists(path):
        msg = f"warning: {spec['csv']} not found in {data_dir}; skipping {table}"
        print(msg, file=sys.stderr)
        out.append(f"-- SKIPPED {table}: {spec['csv']} not found")
        return 0
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        expected = [c for c, _d, _k in spec["columns"]]
        if reader.fieldnames != expected:
            raise SystemExit(
                f"{spec['csv']}: header mismatch.\n  expected: {expected}\n"
                f"  found:    {reader.fieldnames}"
            )
        rows = list(reader)
    cols = db_columns(spec)
    out.append(f"-- {table} <- {spec['csv']} ({len(rows)} rows)")
    # Refresh semantics: clear the table inside its first transaction batch.
    batches = [rows[i : i + batch_size] for i in range(0, len(rows), batch_size)]
    if not batches:
        out.append("BEGIN TRANSACTION;")
        out.append(f"DELETE FROM {table};")
        out.append("COMMIT;")
        return 0
    for bi, batch in enumerate(batches):
        out.append("BEGIN TRANSACTION;")
        if bi == 0:
            out.append(f"DELETE FROM {table};")
        tuples = []
        for j, row in enumerate(batch):
            vals = row_values(row, spec, table, bi * batch_size + j + 2)
            tuples.append("(" + ", ".join(sql_literal(v) for v in vals) + ")")
        out.append(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES\n"
            + ",\n".join(tuples)
            + ";"
        )
        out.append("COMMIT;")
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--data-dir", default=os.path.join(root, "data"))
    ap.add_argument(
        "--out",
        default=os.path.join(root, "output", "d1_import", "registry_import.sql"),
    )
    ap.add_argument("--only", default="", help="comma-separated table names")
    ap.add_argument("--batch-size", type=int, default=500)
    args = ap.parse_args()
    if args.batch_size < 1 or args.batch_size > 500:
        raise SystemExit("--batch-size must be between 1 and 500")

    only = [t.strip() for t in args.only.split(",") if t.strip()]
    unknown = [t for t in only if t not in TABLES]
    if unknown:
        raise SystemExit(f"unknown table(s): {unknown}; known: {sorted(TABLES)}")
    selected = only if only else list(TABLES)

    out = [
        "-- nst registry import (generated by scripts/import_registry_to_d1.py)",
        "-- Apply worker/schema_registry.sql first, then this file.",
        f"-- Source data dir: {args.data_dir}",
    ]
    counts = {}
    for table in selected:
        counts[table] = emit_table(out, table, TABLES[table], args.data_dir, args.batch_size)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    for table, n in counts.items():
        print(f"{table}: {n} rows")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
