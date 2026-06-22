"""Run deterministic Route Intelligence for an explicitly bounded cohort.

Dry-run is the default. A target is always required; there is no all-records
mode. Examples:

    python -m scripts.route_intelligence --lead-id <uuid>
    python -m scripts.route_intelligence --seeded
    python -m scripts.route_intelligence --top-mauritius 50
    python -m scripts.route_intelligence --top-high-fit 100 --matching-only
    python -m scripts.route_intelligence --seeded --write
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from uuid import UUID

from psycopg.types.json import Jsonb

from src.db import get_conn
from src.route_intelligence import (
    build_registered_office_clusters,
    build_route_recommendation,
    match_introducers,
    normalise_address,
)


def _args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--lead-id", type=UUID)
    target.add_argument("--seeded", action="store_true")
    target.add_argument("--top-mauritius", type=int, metavar="N")
    target.add_argument("--top-high-fit", type=int, metavar="N")
    parser.add_argument("--matching-only", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--delay-ms", type=int, default=100)
    args = parser.parse_args(argv)
    if args.top_mauritius is not None and not 1 <= args.top_mauritius <= 50:
        parser.error("--top-mauritius must be between 1 and 50")
    if args.top_high_fit is not None and not 1 <= args.top_high_fit <= 100:
        parser.error("--top-high-fit must be between 1 and 100")
    if not 0 <= args.delay_ms <= 5000:
        parser.error("--delay-ms must be between 0 and 5000")
    return args


def _lead_where(args: argparse.Namespace) -> tuple[str, list[object], int]:
    if args.lead_id:
        return "c.id = %s", [args.lead_id], 1
    if args.seeded:
        return (
            """c.id IN (
                SELECT DISTINCT entity_id FROM audit_log
                WHERE entity_type = 'company'
                  AND action = 'rm_action_updated'
                  AND new_value->>'notes' LIKE 'RM launch seed.%%'
            )""",
            [],
            17,
        )
    if args.top_mauritius is not None:
        return "c.jurisdiction = 'Mauritius' AND ls.tier = 'HIGH'", [], args.top_mauritius
    return "ls.tier = 'HIGH'", [], args.top_high_fit


def _load_leads(conn, args: argparse.Namespace) -> list[dict[str, object]]:
    where, params, limit = _lead_where(args)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT c.id, c.company_name, c.jurisdiction, c.entity_type,
                   c.registered_address, c.verify_url, c.raw_data,
                   COALESCE(cc.website, c.website), cc.generic_email,
                   cc.contact_form_url, cc.linkedin_url, cc.confidence,
                   cc.verified_at, cc.checked_by,
                   ls.priority_score, ls.tier, ra.assigned_to
            FROM companies c
            JOIN lead_scores ls ON ls.company_id = c.id AND ls.is_current = TRUE
            LEFT JOIN company_contacts cc ON cc.company_id = c.id
            LEFT JOIN rm_actions ra ON ra.company_id = c.id
            WHERE {where}
            ORDER BY ls.priority_score DESC, c.company_name
            LIMIT %s
            """,
            params + [limit],
        )
        rows = cur.fetchall()
    leads = []
    for row in rows:
        raw_data = row[6] or {}
        route_context = raw_data.get("registered_office_import") or {}
        leads.append(
            {
            "company_id": str(row[0]),
            "company_name": row[1],
            "jurisdiction": row[2],
            "entity_type": row[3],
            "registered_address": row[4],
            "verify_url": row[5],
            "management_company": route_context.get("management_company"),
            "administrator": route_context.get("administrator"),
            "company_secretary": route_context.get("company_secretary"),
            "website": row[7],
            "generic_email": row[8],
            "contact_form_url": row[9],
            "linkedin_url": row[10],
            "contact_confidence": (
                "high"
                if row[11] is not None and float(row[11]) >= 0.8
                else "medium"
                if row[11] is not None and float(row[11]) >= 0.5
                else "low"
                if row[11] is not None
                else None
            ),
            "last_checked": row[12],
            "checked_by": row[13],
            "priority_score": row[14],
            "tier": row[15],
            "owner": row[16],
            }
        )
    return leads


def _introducer_columns(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'introducers'
            """
        )
        return {row[0] for row in cur.fetchall()}


def _load_introducers(conn) -> list[dict[str, object]]:
    columns = _introducer_columns(conn)
    optional = [
        column if column in columns else f"NULL::text AS {column}"
        for column in (
            "introducer_type",
            "email_domain",
            "website",
            "website_domain",
            "normalized_address",
        )
    ]
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT id, company_name, normalised_name, jurisdiction, category,
                   contact_name, contact_email, phone_number, address,
                   verify_url, source, {', '.join(optional)}
            FROM introducers
            ORDER BY company_name
        """)
        rows = cur.fetchall()
    return [
        {
            "id": str(row[0]),
            "company_name": row[1],
            "normalised_name": row[2],
            "jurisdiction": row[3],
            "category": row[4],
            "contact_name": row[5],
            "contact_email": row[6],
            "phone_number": row[7],
            "address": row[8],
            "verify_url": row[9],
            "source": row[10],
            "introducer_type": row[11],
            "email_domain": row[12],
            "website": row[13],
            "website_domain": row[14],
            "normalized_address": row[15],
        }
        for row in rows
    ]


def _load_address_population(conn) -> list[dict[str, object]]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, registered_address FROM companies
            WHERE jurisdiction = 'Mauritius'
        """)
        return [
            {"company_id": str(row[0]), "registered_address": row[1]}
            for row in cur.fetchall()
        ]


def _write_match(conn, match: dict[str, object]) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO introducer_matches (
                lead_id, introducer_id, match_type, match_strength,
                evidence, source, status, fingerprint
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (fingerprint) DO NOTHING
            """,
            (
                match["lead_id"],
                match["introducer_id"],
                match["match_type"],
                match["match_strength"],
                match["evidence"],
                match["source"],
                match["status"],
                match["fingerprint"],
            ),
        )
        return cur.rowcount


def _write_recommendation(conn, item: dict[str, object]) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE route_recommendations
            SET status = 'superseded'
            WHERE lead_id = %s AND status = 'suggested' AND fingerprint <> %s
            """,
            (item["lead_id"], item["fingerprint"]),
        )
        cur.execute(
            """
            INSERT INTO route_recommendations (
                lead_id, contactability_bucket, best_route_type,
                best_route_value, route_candidate_id, rationale,
                evidence_summary, missing_data, next_action, confidence,
                generated_by, status, fingerprint
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (fingerprint) DO NOTHING
            """,
            (
                item["lead_id"],
                item["contactability_bucket"],
                item["best_route_type"],
                item["best_route_value"],
                item["route_candidate_id"],
                item["rationale"],
                Jsonb(item["evidence_summary"]),
                Jsonb(item["missing_data"]),
                item["next_action"],
                item["confidence"],
                item["generated_by"],
                item["status"],
                item["fingerprint"],
            ),
        )
        return cur.rowcount


def run(args: argparse.Namespace) -> dict[str, object]:
    started = time.perf_counter()
    with get_conn() as conn:
        leads = _load_leads(conn, args)
        introducers = _load_introducers(conn)
        clusters = build_registered_office_clusters(_load_address_population(conn))
        matches_by_lead: dict[str, list[dict[str, object]]] = {}
        recommendations: list[dict[str, object]] = []
        all_matches: list[dict[str, object]] = []
        for lead in leads:
            matches = match_introducers(lead, introducers)
            matches_by_lead[str(lead["company_id"])] = matches
            all_matches.extend(matches)
            if not args.matching_only:
                address_key = normalise_address(str(lead.get("registered_address") or ""))
                recommendations.append(
                    build_route_recommendation(
                        lead=lead,
                        introducer_matches=matches,
                        office_cluster_size=len(clusters.get(address_key, [])),
                    )
                )

        matches_written = 0
        recommendations_written = 0
        if args.write:
            for match in all_matches:
                matches_written += _write_match(conn, match)
            for item in recommendations:
                recommendations_written += _write_recommendation(conn, item)
                if args.delay_ms:
                    time.sleep(args.delay_ms / 1000)
            conn.commit()
        else:
            conn.rollback()

    bucket_counts = Counter(
        str(item["contactability_bucket"]) for item in recommendations
    )
    selected_cluster_keys = {
        normalise_address(str(lead.get("registered_address") or ""))
        for lead in leads
        if normalise_address(str(lead.get("registered_address") or "")) in clusters
    }
    named_candidates = sum(
        1 for item in recommendations if item.get("best_route_value")
    )
    no_route = sum(
        1
        for item in recommendations
        if item["contactability_bucket"]
        in {"registry_evidence_only", "needs_route_research", "no_usable_route"}
        or (
            item["contactability_bucket"] == "management_company_route_likely"
            and not item.get("best_route_value")
        )
    )
    samples = [
        {
            "lead": lead["company_name"],
            "bucket": recommendation["contactability_label"],
            "best_route": recommendation["best_route_type"],
            "candidate": recommendation["best_route_value"],
            "confidence": recommendation["confidence"],
        }
        for lead, recommendation in zip(leads, recommendations, strict=False)
    ][:5]
    return {
        "dry_run": not args.write,
        "matching_only": args.matching_only,
        "leads_evaluated": len(leads),
        "introducers_considered": len(introducers),
        "named_route_candidates": named_candidates,
        "introducer_matches": len(all_matches),
        "registered_office_clusters": len(selected_cluster_keys),
        "no_route_leads": no_route,
        "bucket_counts": dict(sorted(bucket_counts.items())),
        "matches_written": matches_written,
        "recommendations_written": recommendations_written,
        "samples": samples,
        "duration_seconds": round(time.perf_counter() - started, 2),
    }


def main() -> None:
    print(json.dumps(run(_args()), indent=2, default=str))


if __name__ == "__main__":
    main()
