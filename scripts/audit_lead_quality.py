"""Read-only lead-quality audit for the ARIE Prospect Action Desk.

Runs the deterministic route-intelligence engine and the RM-readiness gate over
a bounded cohort of real leads and reports how many are genuinely RM-ready, what
routes/evidence exist, and where the data gaps are. It NEVER writes: the
connection is rolled back before close.

    python -m scripts.audit_lead_quality --limit 200
    python -m scripts.audit_lead_quality --limit 100 --json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter

from src.db import get_conn
from src.route_intelligence import (
    build_registered_office_clusters,
    build_route_recommendation,
    contactability_status,
    lead_readiness,
    match_introducers,
    normalise_address,
    normalise_name,
)
from scripts.route_intelligence import _load_address_population, _load_introducers

_STALE_DAYS = 180


def _args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--json", action="store_true", help="emit raw JSON only")
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 2000:
        parser.error("--limit must be between 1 and 2000")
    return args


def _load_leads_for_audit(conn, limit: int) -> list[dict[str, object]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.id, c.company_name, c.jurisdiction, c.entity_type,
                   c.registered_address, c.verify_url,
                   COALESCE(cc.website, c.website), cc.generic_email,
                   cc.contact_form_url, cc.linkedin_url, cc.confidence,
                   cc.verified_at,
                   ls.priority_score, ls.tier
            FROM companies c
            JOIN lead_scores ls ON ls.company_id = c.id AND ls.is_current = TRUE
            LEFT JOIN company_contacts cc ON cc.company_id = c.id
            ORDER BY ls.priority_score DESC NULLS LAST, c.company_name
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
    leads = []
    for row in rows:
        conf = row[10]
        leads.append(
            {
                "company_id": str(row[0]),
                "company_name": row[1],
                "jurisdiction": row[2],
                "entity_type": row[3],
                "registered_address": row[4],
                "verify_url": row[5],
                "website": row[6],
                "generic_email": row[7],
                "contact_form_url": row[8],
                "linkedin_url": row[9],
                "contact_confidence": (
                    "high"
                    if conf is not None and float(conf) >= 0.8
                    else "medium"
                    if conf is not None and float(conf) >= 0.5
                    else "low"
                    if conf is not None
                    else None
                ),
                "last_checked": row[11],
                "priority_score": row[12],
                "tier": row[13],
            }
        )
    return leads


def audit(limit: int) -> dict[str, object]:
    with get_conn() as conn:
        leads = _load_leads_for_audit(conn, limit)
        introducers = _load_introducers(conn)
        clusters = build_registered_office_clusters(_load_address_population(conn))

        evaluated = []
        for lead in leads:
            matches = match_introducers(lead, introducers)
            address_key = normalise_address(str(lead.get("registered_address") or ""))
            rec = build_route_recommendation(
                lead=lead,
                introducer_matches=matches,
                office_cluster_size=len(clusters.get(address_key, [])),
            )
            gate = lead_readiness(recommendation=rec, tier=lead.get("tier"))
            evaluated.append((lead, matches, rec, gate))

        conn.rollback()  # read-only guarantee

    total = len(evaluated)
    status_counts: Counter[str] = Counter()
    readiness_counts: Counter[str] = Counter()
    with_evidence = with_named_route = with_introducer = 0
    missing_contact_route = stale = 0
    name_keys: Counter[str] = Counter()

    for lead, matches, rec, gate in evaluated:
        status_counts[contactability_status(str(rec["contactability_bucket"]))] += 1
        readiness_counts[str(gate["readiness"])] += 1
        if rec.get("evidence_summary"):
            with_evidence += 1
        if rec.get("best_route_value"):
            with_named_route += 1
        if matches:
            with_introducer += 1
        if not (lead.get("website") or lead.get("generic_email") or lead.get("contact_form_url")):
            missing_contact_route += 1
        if (lead.get("website") or lead.get("generic_email")) and lead.get("last_checked") is None:
            stale += 1
        name_keys[normalise_name(str(lead.get("company_name") or ""), strip_legal_suffixes=True)] += 1

    duplicates = sum(count - 1 for count in name_keys.values() if count > 1)

    def _rank(item):
        lead, _matches, rec, gate = item
        status = contactability_status(str(rec["contactability_bucket"]))
        status_rank = {"ready_to_contact": 0, "route_via_introducer": 1}.get(status, 2)
        conf_rank = {"high": 0, "medium": 1, "low": 2, "not_usable": 3}.get(
            str(rec.get("confidence")), 3
        )
        return (
            0 if gate["is_rm_ready"] else 1,
            status_rank,
            conf_rank,
            -(lead.get("priority_score") or 0),
        )

    top = sorted(evaluated, key=_rank)[:10]
    top_opportunities = [
        {
            "company": lead["company_name"],
            "jurisdiction": lead["jurisdiction"],
            "tier": lead["tier"],
            "score": lead["priority_score"],
            "decision_status": contactability_status(str(rec["contactability_bucket"])),
            "best_route": rec.get("best_route_value") or rec.get("best_route_type"),
            "confidence": rec.get("confidence"),
            "rm_ready": gate["is_rm_ready"],
            "next_action": rec.get("next_action"),
        }
        for lead, _m, rec, gate in top
    ]

    actionable = readiness_counts.get("rm_ready", 0) + status_counts.get(
        "route_via_introducer", 0
    )
    return {
        "total_reviewed": total,
        "rm_ready": readiness_counts.get("rm_ready", 0),
        "research_first": readiness_counts.get("research_first", 0),
        "no_compliant_route": readiness_counts.get("no_compliant_route", 0),
        "status_ready_to_contact": status_counts.get("ready_to_contact", 0),
        "status_route_via_introducer": status_counts.get("route_via_introducer", 0),
        "status_research_required": status_counts.get("research_required", 0),
        "status_no_compliant_route": status_counts.get("no_compliant_route_found", 0),
        "with_introducer_match": with_introducer,
        "with_named_route": with_named_route,
        "with_evidence": with_evidence,
        "missing_contact_route": missing_contact_route,
        "stale_contact_data": stale,
        "duplicate_like_records": duplicates,
        "pct_actionable": round(actionable * 100 / total, 1) if total else 0.0,
        "pct_with_evidence": round(with_evidence * 100 / total, 1) if total else 0.0,
        "pct_with_named_route": round(with_named_route * 100 / total, 1) if total else 0.0,
        "top_opportunities": top_opportunities,
    }


def main() -> None:
    args = _args()
    result = audit(args.limit)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
