"""Export the best leads for Manus / manual contact-route research.

Read-only. Selects HIGH/MEDIUM leads that are NOT yet RM-ready and lack a
compliant contact route, runs the deterministic engine to explain why, and
writes a Manus-ready CSV plus a terminal summary. It never writes to the DB.

    python -m scripts.export_research_batch
    python -m scripts.export_research_batch --limit 100 --output batch.csv
"""

from __future__ import annotations

import argparse
import csv

from src.db import get_conn
from src.route_intelligence import (
    build_registered_office_clusters,
    build_route_recommendation,
    contactability_status,
    lead_readiness,
    match_introducers,
    normalise_address,
)
from scripts.route_intelligence import _load_address_population, _load_introducers

EXPORT_COLUMNS = [
    "company_id",
    "company_number",
    "company_name",
    "jurisdiction",
    "entity_type",
    "incorporation_date",
    "score",
    "score_tier",
    "current_contactability_status",
    "current_route_bucket",
    "why_this_lead",
    "why_now",
    "registry_url",
    "officers_summary",
    "psc_summary",
    "registered_address",
    "existing_website_url",
    "existing_contact_route",
    "missing_reason",
    "research_instruction",
]

_RESEARCH_NEEDED = {"research_required", "no_compliant_route_found"}


def _args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--output", default="research_batch.csv")
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 1000:
        parser.error("--limit must be between 1 and 1000")
    return args


def _why_now(reason_codes: list[str] | None) -> str:
    codes = set(reason_codes or [])
    bits = []
    if "FRESH_LEI" in codes:
        bits.append("Fresh LEI registration")
    if "RECENTLY_INCORPORATED" in codes:
        bits.append("Recently incorporated entity")
    return " · ".join(bits)


def _research_instruction(lead: dict[str, object], bucket: str) -> str:
    name = lead.get("company_name") or "this company"
    if bucket == "management_company_route_likely":
        return (
            f"Find the management company / CSP / administrator that represents {name} "
            "(check FSC register and the official website). Return the intermediary "
            "name + source URL."
        )
    return (
        f"Find an official company route for {name}: website, contact page, contact "
        "form, or a generic company email from the official site. Return each with a "
        "source URL. Do not guess personal emails."
    )


def build_research_rows(
    leads: list[dict[str, object]],
    introducers: list[dict[str, object]],
    clusters: dict[str, list[str]],
    limit: int,
) -> list[dict[str, object]]:
    """Pure: rank research-needed leads and shape export rows. No DB, no I/O."""
    rows: list[dict[str, object]] = []
    for lead in leads:
        matches = match_introducers(lead, introducers)
        address_key = normalise_address(str(lead.get("registered_address") or ""))
        rec = build_route_recommendation(
            lead=lead,
            introducer_matches=matches,
            office_cluster_size=len(clusters.get(address_key, [])),
        )
        gate = lead_readiness(recommendation=rec, tier=lead.get("tier"))
        status = contactability_status(str(rec["contactability_bucket"]))
        # Only export leads that actually need research (not already actionable).
        if gate["is_rm_ready"] or status == "route_via_introducer":
            continue
        if str(rec["contactability_bucket"]) not in (
            "direct_candidate_found",
            "management_company_route_likely",
            "registry_evidence_only",
            "needs_route_research",
            "no_usable_route",
        ):
            continue
        existing_route = (
            lead.get("generic_email")
            or lead.get("contact_form_url")
            or lead.get("website")
            or ""
        )
        rows.append(
            {
                "company_id": lead.get("company_id"),
                "company_number": lead.get("company_number") or "",
                "company_name": lead.get("company_name"),
                "jurisdiction": lead.get("jurisdiction"),
                "entity_type": lead.get("entity_type") or "",
                "incorporation_date": lead.get("incorporation_date") or "",
                "score": lead.get("priority_score"),
                "score_tier": lead.get("tier"),
                "current_contactability_status": status,
                "current_route_bucket": rec["contactability_bucket"],
                "why_this_lead": lead.get("reason_summary") or "",
                "why_now": _why_now(lead.get("reason_codes")),
                "registry_url": lead.get("verify_url") or "",
                "officers_summary": lead.get("officers_summary") or "",
                "psc_summary": lead.get("psc_summary") or "",
                "registered_address": lead.get("registered_address") or "",
                "existing_website_url": lead.get("website") or "",
                "existing_contact_route": existing_route,
                "missing_reason": "; ".join(gate["blocking_reasons"])
                or "; ".join(rec.get("missing_data") or []),
                "research_instruction": _research_instruction(
                    lead, str(rec["contactability_bucket"])
                ),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _load_candidate_leads(conn, limit: int) -> list[dict[str, object]]:
    # Pull a generous candidate pool (filtering to research-needed happens after
    # the engine runs); cap to keep it bounded.
    pool = min(max(limit * 6, 100), 1200)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.id, c.source_ref, c.company_name, c.jurisdiction, c.entity_type,
                   c.incorporation_date, c.registered_address, c.verify_url,
                   COALESCE(cc.website, c.website), cc.generic_email,
                   cc.contact_form_url, cc.linkedin_url, cc.confidence,
                   ls.priority_score, ls.tier, ls.reason_codes, ls.reason_summary,
                   (SELECT string_agg(officer_name, '; ') FROM (
                        SELECT officer_name FROM company_officers
                        WHERE company_id = c.id AND resigned_on IS NULL
                        ORDER BY appointed_on DESC NULLS LAST LIMIT 3) o),
                   (SELECT string_agg(name, '; ') FROM (
                        SELECT name FROM company_pscs
                        WHERE company_id = c.id AND ceased_on IS NULL
                        ORDER BY notified_on DESC NULLS LAST LIMIT 3) p)
            FROM companies c
            JOIN lead_scores ls ON ls.company_id = c.id AND ls.is_current = TRUE
            LEFT JOIN company_contacts cc ON cc.company_id = c.id
            WHERE ls.tier IN ('HIGH', 'MEDIUM')
            ORDER BY ls.priority_score DESC NULLS LAST, c.company_name
            LIMIT %s
            """,
            (pool,),
        )
        rows = cur.fetchall()
    leads = []
    for row in rows:
        conf = row[12]
        leads.append(
            {
                "company_id": str(row[0]),
                "company_number": row[1],
                "company_name": row[2],
                "jurisdiction": row[3],
                "entity_type": row[4],
                "incorporation_date": row[5],
                "registered_address": row[6],
                "verify_url": row[7],
                "website": row[8],
                "generic_email": row[9],
                "contact_form_url": row[10],
                "linkedin_url": row[11],
                "contact_confidence": (
                    "high"
                    if conf is not None and float(conf) >= 0.8
                    else "medium"
                    if conf is not None and float(conf) >= 0.5
                    else "low"
                    if conf is not None
                    else None
                ),
                "priority_score": row[13],
                "tier": row[14],
                "reason_codes": row[15],
                "reason_summary": row[16],
                "officers_summary": row[17],
                "psc_summary": row[18],
            }
        )
    return leads


def run(limit: int, output: str) -> dict[str, object]:
    with get_conn() as conn:
        leads = _load_candidate_leads(conn, limit)
        introducers = _load_introducers(conn)
        clusters = build_registered_office_clusters(_load_address_population(conn))
        conn.rollback()  # read-only guarantee

    rows = build_research_rows(leads, introducers, clusters, limit)
    with open(output, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    missing_route = sum(
        1 for r in rows if not r["existing_contact_route"]
    )
    likely_introducer = sum(
        1
        for r in rows
        if r["current_route_bucket"] == "management_company_route_likely"
    )
    return {
        "rows_exported": len(rows),
        "missing_contact_route": missing_route,
        "likely_introducer_csp_route": likely_introducer,
        "output_path": output,
    }


def main() -> None:
    args = _args()
    summary = run(args.limit, args.output)
    print(f"Exported {summary['rows_exported']} research leads -> {summary['output_path']}")
    print(f"  missing contact route : {summary['missing_contact_route']}")
    print(f"  likely introducer/CSP : {summary['likely_introducer_csp_route']}")
    print("Next: upload the CSV to Manus using docs/manus_contact_research_prompt.md")


if __name__ == "__main__":
    main()
