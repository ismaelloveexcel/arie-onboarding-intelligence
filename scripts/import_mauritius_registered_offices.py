"""Import operator-reviewed Mauritius registered-office data from CSV.

Dry-run is the default. Examples:

    python -m scripts.import_mauritius_registered_offices --csv offices.csv --dry-run
    python -m scripts.import_mauritius_registered_offices --csv offices.csv --write
    python -m scripts.import_mauritius_registered_offices --csv offices.csv --write --replace
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

from psycopg.types.json import Jsonb

from src.db import get_conn
from src.route_intelligence import normalise_name

_REQUIRED_COLUMNS = {
    "company_name",
    "registered_office_address",
    "source",
    "checked_at",
    "confidence",
}
_OPTIONAL_CONTEXT = ("management_company", "administrator", "company_secretary", "notes")
_CONFIDENCE = {"high", "medium", "low"}


def _args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--write", action="store_true")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)
    if args.replace and not args.write:
        parser.error("--replace requires --write")
    return args


def _parse_checked_at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("checked_at must include a timezone")
    return parsed


def _validate_headers(fieldnames: list[str] | None) -> None:
    headers = set(fieldnames or [])
    missing = sorted(_REQUIRED_COLUMNS - headers)
    if missing:
        raise ValueError(f"Missing required CSV columns: {', '.join(missing)}")
    if not {"registry_file_number", "company_number"} & headers:
        raise ValueError(
            "CSV requires registry_file_number or company_number for registry matching"
        )


def _clean_row(row: dict[str, str], row_number: int) -> dict[str, object]:
    confidence = (row.get("confidence") or "").strip().casefold()
    if confidence not in _CONFIDENCE:
        raise ValueError(f"row {row_number}: confidence must be High, Medium, or Low")
    address = (row.get("registered_office_address") or "").strip()
    source = (row.get("source") or "").strip()
    company_name = (row.get("company_name") or "").strip()
    if not address or not source or not company_name:
        raise ValueError(
            f"row {row_number}: company_name, registered_office_address, and source are required"
        )
    identifier = (
        row.get("registry_file_number") or row.get("company_number") or ""
    ).strip()
    context = {
        key: (row.get(key) or "").strip()
        for key in _OPTIONAL_CONTEXT
        if (row.get(key) or "").strip()
    }
    return {
        "row_number": row_number,
        "identifier": identifier,
        "company_name": company_name,
        "normalised_name": normalise_name(company_name),
        "registered_address": address,
        "source": source,
        "checked_at": _parse_checked_at(row.get("checked_at") or ""),
        "confidence": confidence,
        "context": context,
    }


def read_csv(path: Path) -> tuple[list[dict[str, object]], list[str]]:
    rows: list[dict[str, object]] = []
    errors: list[str] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        _validate_headers(reader.fieldnames)
        for row_number, raw_row in enumerate(reader, start=2):
            try:
                rows.append(_clean_row(raw_row, row_number))
            except (ValueError, TypeError) as exc:
                errors.append(str(exc))
    return rows, errors


def _find_company(conn, row: dict[str, object]) -> tuple[str, list[tuple]]:
    with conn.cursor() as cur:
        if row["identifier"]:
            cur.execute(
                """
                SELECT id, company_name, registered_address
                FROM companies
                WHERE jurisdiction = 'Mauritius'
                  AND (source_ref = %s OR raw_data->>'registration_number' = %s)
                ORDER BY company_name
                """,
                (row["identifier"], row["identifier"]),
            )
            return "registry_file_number", cur.fetchall()
        cur.execute(
            """
            SELECT id, company_name, registered_address
            FROM companies
            WHERE jurisdiction = 'Mauritius' AND normalised_name = %s
            ORDER BY company_name
            """,
            (row["normalised_name"],),
        )
        return "exact_normalised_name", cur.fetchall()


def _write_company(conn, company_id, row: dict[str, object]) -> None:
    context = {
        **row["context"],
        "source": row["source"],
        "checked_at": row["checked_at"].isoformat(),
        "confidence": row["confidence"],
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE companies
            SET registered_address = %s,
                registered_office_source = %s,
                registered_office_checked_at = %s,
                registered_office_confidence = %s,
                registered_office_retrieval_status = 'found',
                raw_data = COALESCE(raw_data, '{}'::jsonb)
                    || jsonb_build_object('registered_office_import', %s::jsonb),
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                row["registered_address"],
                row["source"],
                row["checked_at"],
                row["confidence"],
                Jsonb(context),
                company_id,
            ),
        )


def run(args: argparse.Namespace) -> dict[str, object]:
    rows, validation_errors = read_csv(args.csv)
    report: dict[str, object] = {
        "dry_run": not args.write,
        "replace": args.replace,
        "rows_read": len(rows) + len(validation_errors),
        "valid_rows": len(rows),
        "validation_errors": validation_errors,
        "matched_by_file_number": 0,
        "matched_by_name": 0,
        "rows_to_update": 0,
        "rows_updated": 0,
        "unmatched_rows": [],
        "ambiguous_rows": [],
        "skipped_existing": [],
    }
    with get_conn() as conn:
        for row in rows:
            basis, matches = _find_company(conn, row)
            if not matches:
                report["unmatched_rows"].append(
                    {"row": row["row_number"], "company_name": row["company_name"]}
                )
                continue
            if len(matches) > 1:
                report["ambiguous_rows"].append(
                    {
                        "row": row["row_number"],
                        "company_name": row["company_name"],
                        "matches": [match[1] for match in matches],
                    }
                )
                continue
            company_id, matched_name, existing_address = matches[0]
            report[
                "matched_by_file_number"
                if basis == "registry_file_number"
                else "matched_by_name"
            ] += 1
            if existing_address and not args.replace:
                report["skipped_existing"].append(
                    {"row": row["row_number"], "company_name": matched_name}
                )
                continue
            report["rows_to_update"] += 1
            if args.write:
                _write_company(conn, company_id, row)
                report["rows_updated"] += 1
        if args.write:
            conn.commit()
        else:
            conn.rollback()
    return report


def main() -> None:
    print(json.dumps(run(_args()), indent=2, default=str))


if __name__ == "__main__":
    main()
