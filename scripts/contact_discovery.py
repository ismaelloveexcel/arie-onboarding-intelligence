"""Persist concrete Contact Discovery candidates.

The current offline workflow does not fetch public pages, so it intentionally
produces no candidates. Research shortcuts are rendered on the lead page and
must never be inserted as reviewable suggestions.

Dry run is the default and never writes. Examples:

    python -m scripts.contact_discovery --limit 20
    python -m scripts.contact_discovery --lead-id <uuid>
    python -m scripts.contact_discovery --limit 50 --write
"""

from __future__ import annotations

import argparse
import json
import time
from uuid import UUID

from src.contact_discovery import build_contact_discovery_suggestions
from src.db import get_conn


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--lead-id", type=UUID)
    target.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Append suggestions. Without this flag the command is a dry run.",
    )
    parser.add_argument("--delay-ms", type=int, default=200)
    args = parser.parse_args()
    if not 1 <= args.limit <= 50:
        parser.error("--limit must be between 1 and 50")
    if not 0 <= args.delay_ms <= 5000:
        parser.error("--delay-ms must be between 0 and 5000")
    return args


def _load_leads(conn, *, lead_id: UUID | None, limit: int) -> list[dict]:
    where = "c.id = %s" if lead_id else "ls.tier = 'HIGH' AND COALESCE(ls.reachability_status, 'no_contact_path') = 'no_contact_path'"
    params: list[object] = [lead_id] if lead_id else []
    params.append(1 if lead_id else limit)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT c.id, c.company_name, c.jurisdiction, c.source_ref,
                   c.registered_address, c.verify_url,
                   ARRAY_REMOVE(ARRAY_AGG(o.officer_name)
                       FILTER (WHERE o.resigned_on IS NULL), NULL) AS officer_names
            FROM companies c
            JOIN lead_scores ls ON ls.company_id = c.id AND ls.is_current = TRUE
            LEFT JOIN company_officers o ON o.company_id = c.id
            WHERE {where}
            GROUP BY c.id, c.company_name, c.jurisdiction, c.source_ref,
                     c.registered_address, c.verify_url, ls.score
            ORDER BY ls.score DESC, c.company_name
            LIMIT %s
            """,
            params,
        )
        rows = cur.fetchall()
    return [
        {
            "company_id": str(row[0]),
            "company_name": row[1],
            "jurisdiction": row[2],
            "source_ref": row[3],
            "registered_address": row[4],
            "verify_url": row[5],
            "officer_names": row[6] or [],
        }
        for row in rows
    ]


def _insert_suggestions(conn, suggestions: list[dict[str, str]]) -> int:
    inserted = 0
    with conn.cursor() as cur:
        for item in suggestions:
            cur.execute(
                """
                INSERT INTO contact_discovery_suggestions (
                    company_id, suggestion_type, suggested_value, source_name,
                    source_url, search_query, confidence, confidence_reason,
                    status, fingerprint
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (fingerprint) DO NOTHING
                """,
                (
                    item["company_id"],
                    item["suggestion_type"],
                    item["suggested_value"],
                    item["source_name"],
                    item["source_url"],
                    item["search_query"],
                    item["confidence"],
                    item["confidence_reason"],
                    item["status"],
                    item["fingerprint"],
                ),
            )
            inserted += cur.rowcount
    return inserted


def main() -> None:
    args = _args()
    output: list[dict] = []
    inserted = 0
    with get_conn() as conn:
        leads = _load_leads(conn, lead_id=args.lead_id, limit=args.limit)
        for lead in leads:
            suggestions = build_contact_discovery_suggestions(**lead)
            output.append({"lead": lead["company_name"], "suggestions": suggestions})
            if args.write:
                inserted += _insert_suggestions(conn, suggestions)
                if args.delay_ms:
                    time.sleep(args.delay_ms / 1000)
        if args.write:
            conn.commit()
        else:
            conn.rollback()
    print(json.dumps({"dry_run": not args.write, "inserted": inserted, "leads": output}, indent=2))


if __name__ == "__main__":
    main()
