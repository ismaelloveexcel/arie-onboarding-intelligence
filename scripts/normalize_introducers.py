"""Derive conservative introducer route fields from stored evidence.

Dry-run is the default. Examples:

    python -m scripts.normalize_introducers --dry-run
    python -m scripts.normalize_introducers --write
"""

from __future__ import annotations

import argparse
import json
from collections import Counter

from src.db import get_conn
from src.route_intelligence import normalise_introducer

_WRITE_COLUMNS = {
    "email_domain",
    "website",
    "website_domain",
    "normalized_address",
    "introducer_type",
    "category_source",
    "category_confidence",
}


def _args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--write", action="store_true")
    return parser.parse_args(argv)


def _columns(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'introducers'
            """
        )
        return {row[0] for row in cur.fetchall()}


def _load_introducers(conn, columns: set[str]) -> list[dict[str, object]]:
    website_sql = "website" if "website" in columns else "NULL::text AS website"
    current_sql = [
        column if column in columns else f"NULL::text AS {column}"
        for column in sorted(_WRITE_COLUMNS - {"website"})
    ]
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT id, company_name, category, source, contact_email, address,
                   verify_url, {website_sql}, {', '.join(current_sql)}
            FROM introducers
            ORDER BY company_name
        """)
        rows = cur.fetchall()
    current_names = sorted(_WRITE_COLUMNS - {"website"})
    return [
        {
            "id": str(row[0]),
            "company_name": row[1],
            "category": row[2],
            "source": row[3],
            "contact_email": row[4],
            "address": row[5],
            "verify_url": row[6],
            "website": row[7],
            "current": {
                "website": row[7],
                **dict(zip(current_names, row[8:], strict=True)),
            },
        }
        for row in rows
    ]


def _write_introducer(conn, introducer_id: str, values: dict[str, object]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE introducers
            SET email_domain = %s, website = %s, website_domain = %s,
                normalized_address = %s, introducer_type = %s,
                category_source = %s, category_confidence = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                values["email_domain"],
                values["website"],
                values["website_domain"],
                values["normalized_address"],
                values["introducer_type"],
                values["category_source"],
                values["category_confidence"],
                introducer_id,
            ),
        )


def run(args: argparse.Namespace) -> dict[str, object]:
    with get_conn() as conn:
        columns = _columns(conn)
        if args.write and not _WRITE_COLUMNS <= columns:
            missing = ", ".join(sorted(_WRITE_COLUMNS - columns))
            raise RuntimeError(f"Apply the route data migration before --write: {missing}")
        introducers = _load_introducers(conn, columns)
        type_counts: Counter[str] = Counter()
        reason_counts: Counter[str] = Counter()
        changed = 0
        written = 0
        coverage: Counter[str] = Counter()
        classified_samples: list[dict[str, object]] = []
        unknown_samples: list[dict[str, object]] = []
        for introducer in introducers:
            values = normalise_introducer(introducer)
            type_counts[str(values["introducer_type"])] += 1
            reason_counts[str(values["category_reason"])] += 1
            for field in (
                "email_domain",
                "website_domain",
                "normalized_address",
            ):
                if values[field]:
                    coverage[field] += 1
            comparable = {key: values[key] for key in _WRITE_COLUMNS}
            if comparable != introducer["current"]:
                changed += 1
                sample = {
                    "company_name": introducer["company_name"],
                    "introducer_type": values["introducer_type"],
                    "category_confidence": values["category_confidence"],
                    "reason": values["category_reason"],
                }
                if values["introducer_type"] != "unknown" and len(classified_samples) < 10:
                    classified_samples.append(sample)
                elif len(unknown_samples) < 3:
                    unknown_samples.append(sample)
                if args.write:
                    _write_introducer(conn, str(introducer["id"]), values)
                    written += 1
        if args.write:
            conn.commit()
        else:
            conn.rollback()
    return {
        "dry_run": not args.write,
        "introducers_evaluated": len(introducers),
        "rows_changed": changed,
        "rows_written": written,
        "derived_coverage": dict(sorted(coverage.items())),
        "type_counts": dict(sorted(type_counts.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
        "classified_samples": classified_samples,
        "unknown_samples": unknown_samples,
    }


def main() -> None:
    print(json.dumps(run(_args()), indent=2, default=str))


if __name__ == "__main__":
    main()
