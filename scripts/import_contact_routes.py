"""Import manually-researched contact routes (Manus / RM research) safely.

Dry-run by default — it validates every row, previews the readiness movement, and
rolls back. Pass --write to persist (run against staging first, never production).

    python -m scripts.import_contact_routes enriched.csv               # dry-run
    python -m scripts.import_contact_routes enriched.csv --write       # persist
    python -m scripts.import_contact_routes enriched.csv --update-existing

It writes only to existing tables (company_contacts route fields + a refreshed
route_recommendations row). It never creates a second contactability system, never
invents data, and never accepts personal/guessed emails as a verified route.
"""

from __future__ import annotations

import argparse
import csv
import json

from psycopg.types.json import Jsonb

from src.contact_routes import dedupe_key, validate_route_row
from src.db import get_conn
from src.route_intelligence import (
    build_route_recommendation,
    contactability_status,
    lead_readiness,
)

_CONFIDENCE_NUMERIC = {"high": 0.9, "medium": 0.6, "low": 0.3}


def _args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--update-existing", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def _lead_from(company: dict[str, object], norm: dict[str, object]) -> dict[str, object]:
    """Merge stored company data with imported route fields for the engine."""
    return {
        "company_id": company["company_id"],
        "company_name": company.get("company_name"),
        "jurisdiction": company.get("jurisdiction"),
        "entity_type": company.get("entity_type"),
        "registered_address": company.get("registered_address"),
        "verify_url": company.get("verify_url"),
        "website": norm.get("website") or company.get("website"),
        "generic_email": norm.get("generic_email"),
        "contact_form_url": norm.get("contact_form_url"),
        "linkedin_url": norm.get("linkedin_url"),
        "contact_confidence": norm.get("confidence"),
    }


def _synthetic_introducer_match(norm: dict[str, object]) -> list[dict[str, object]]:
    name = norm.get("introducer_or_csp_name")
    if not name:
        return []
    introducer = {"id": None, "company_name": name, "category": norm.get("introducer_or_csp_route") or ""}
    return [
        {
            "introducer": introducer,
            "match_type": "manual",
            "match_strength": norm.get("confidence") or "medium",
            "evidence": norm.get("evidence_summary") or "Manually researched introducer/CSP route.",
            "source": norm.get("source_label") or "manual",
        }
    ]


def evaluate_batch(
    parsed_rows: list[dict[str, str]],
    company_resolver,
    *,
    update_existing: bool = False,
) -> dict[str, object]:
    """Pure evaluation: validate, dedupe, resolve, and predict readiness.

    company_resolver(normalized) -> company dict (with keys company_id,
    company_name, jurisdiction, entity_type, registered_address, verify_url,
    website, tier, has_existing_route) or None. No DB, no I/O here.
    """
    accepted: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []
    duplicates: list[dict[str, object]] = []
    seen: set[str] = set()

    for index, raw in enumerate(parsed_rows):
        result = validate_route_row(raw, update_existing=update_existing)
        if not result["ok"]:
            rejected.append({"row": index + 1, "errors": result["errors"], "raw": raw})
            continue
        norm = result["normalized"]

        key = dedupe_key(norm)
        if key and key in seen:
            duplicates.append({"row": index + 1, "reason": "Duplicate within file", "key": key})
            continue
        seen.add(key)

        company = company_resolver(norm)
        if company is None:
            rejected.append(
                {"row": index + 1, "errors": ["Company not found"], "raw": raw}
            )
            continue
        if company.get("has_existing_route") and not update_existing:
            duplicates.append(
                {"row": index + 1, "reason": "Company already has a route", "key": key}
            )
            continue

        lead = _lead_from(company, norm)
        matches = _synthetic_introducer_match(norm)
        rec = build_route_recommendation(lead=lead, introducer_matches=matches)
        gate = lead_readiness(recommendation=rec, tier=company.get("tier"))
        accepted.append(
            {
                "row": index + 1,
                "company": company,
                "normalized": norm,
                "warnings": result["warnings"],
                "predicted_status": contactability_status(str(rec["contactability_bucket"])),
                "predicted_bucket": rec["contactability_bucket"],
                "is_rm_ready": gate["is_rm_ready"],
                "recommendation": rec,
            }
        )

    moved_ready = [a for a in accepted if a["predicted_status"] == "ready_to_contact"]
    moved_introducer = [
        a for a in accepted if a["predicted_status"] == "route_via_introducer"
    ]
    still_research = [
        a
        for a in accepted
        if a["predicted_status"] in {"research_required", "no_compliant_route_found"}
    ]
    now_rm_ready = sorted(
        (a for a in accepted if a["is_rm_ready"]),
        key=lambda a: -(a["company"].get("priority_score") or 0),
    )[:20]

    return {
        "accepted": accepted,
        "rejected": rejected,
        "duplicates": duplicates,
        "summary": {
            "rows_processed": len(parsed_rows),
            "accepted": len(accepted),
            "rejected": len(rejected),
            "duplicates": len(duplicates),
            "companies_improved": len(accepted),
            "moved_to_ready_to_contact": len(moved_ready),
            "moved_to_route_via_introducer": len(moved_introducer),
            "still_research_required": len(still_research),
        },
        "top_now_rm_ready": [
            {
                "company_name": a["company"].get("company_name"),
                "predicted_status": a["predicted_status"],
                "best_route": a["recommendation"].get("best_route_value")
                or a["recommendation"].get("best_route_type"),
                "confidence": a["recommendation"].get("confidence"),
            }
            for a in now_rm_ready
        ],
    }


# --------------------------------------------------------------------------- DB


def _make_resolver(conn):
    def resolver(norm: dict[str, object]):
        with conn.cursor() as cur:
            if norm.get("company_id"):
                cur.execute(
                    "SELECT id FROM companies WHERE id = %s", (norm["company_id"],)
                )
            elif norm.get("company_number"):
                cur.execute(
                    "SELECT id FROM companies WHERE source_ref = %s",
                    (norm["company_number"],),
                )
            else:
                return None
            found = cur.fetchone()
            if not found:
                return None
            company_id = str(found[0])
            cur.execute(
                """
                SELECT c.company_name, c.jurisdiction, c.entity_type,
                       c.registered_address, c.verify_url, c.website,
                       ls.score, ls.tier,
                       (cc.generic_email IS NOT NULL OR cc.contact_form_url IS NOT NULL
                        OR cc.website IS NOT NULL OR rr.lead_id IS NOT NULL)
                FROM companies c
                LEFT JOIN lead_scores ls
                    ON ls.company_id = c.id AND ls.is_current = TRUE
                LEFT JOIN company_contacts cc ON cc.company_id = c.id
                LEFT JOIN route_recommendations rr
                    ON rr.lead_id = c.id AND rr.status <> 'superseded'
                WHERE c.id = %s
                LIMIT 1
                """,
                (company_id,),
            )
            r = cur.fetchone()
        return {
            "company_id": company_id,
            "company_name": r[0],
            "jurisdiction": r[1],
            "entity_type": r[2],
            "registered_address": r[3],
            "verify_url": r[4],
            "website": r[5],
            "priority_score": r[6],
            "tier": r[7],
            "has_existing_route": bool(r[8]),
        }

    return resolver


def _persist(conn, item: dict[str, object]) -> None:
    norm = item["normalized"]
    company_id = item["company"]["company_id"]
    confidence_numeric = _CONFIDENCE_NUMERIC.get(str(norm.get("confidence")), None)
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE company_contacts
            SET website = COALESCE(%s, website),
                generic_email = COALESCE(%s, generic_email),
                contact_form_url = COALESCE(%s, contact_form_url),
                linkedin_url = COALESCE(%s, linkedin_url),
                confidence = COALESCE(%s, confidence),
                route_source_url = %s, route_source_label = %s,
                route_source_type = %s, route_entry_method = %s,
                verified_at = NOW(), updated_at = NOW()
            WHERE company_id = %s
            """,
            (
                norm.get("website"), norm.get("generic_email"),
                norm.get("contact_form_url"), norm.get("linkedin_url"),
                confidence_numeric, norm.get("source_url"), norm.get("source_label"),
                norm.get("source_type"), norm.get("route_entry_method"), company_id,
            ),
        )
        if cur.rowcount == 0:
            cur.execute(
                """
                INSERT INTO company_contacts
                    (company_id, source, reachability_status, website, generic_email,
                     contact_form_url, linkedin_url, confidence, route_source_url,
                     route_source_label, route_source_type, route_entry_method,
                     verified_at)
                VALUES (%s, %s, 'validated', %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                (
                    company_id, norm.get("source_label") or "manual_import",
                    norm.get("website"), norm.get("generic_email"),
                    norm.get("contact_form_url"), norm.get("linkedin_url"),
                    confidence_numeric, norm.get("source_url"),
                    norm.get("source_label"), norm.get("source_type"),
                    norm.get("route_entry_method"),
                ),
            )

        rec = item["recommendation"]
        cur.execute(
            """
            UPDATE route_recommendations SET status = 'superseded'
            WHERE lead_id = %s AND status = 'suggested' AND fingerprint <> %s
            """,
            (company_id, rec["fingerprint"]),
        )
        cur.execute(
            """
            INSERT INTO route_recommendations (
                lead_id, contactability_bucket, best_route_type, best_route_value,
                route_candidate_id, rationale, evidence_summary, missing_data,
                next_action, confidence, generated_by, status, fingerprint,
                route_source_url, route_source_label, route_source_type,
                route_entry_method)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (fingerprint) DO NOTHING
            """,
            (
                company_id, rec["contactability_bucket"], rec["best_route_type"],
                rec["best_route_value"], None, rec["rationale"],
                Jsonb(rec["evidence_summary"]), Jsonb(rec["missing_data"]),
                rec["next_action"], rec["confidence"], "manual-import", "suggested",
                rec["fingerprint"], norm.get("source_url"), norm.get("source_label"),
                norm.get("source_type"), norm.get("route_entry_method"),
            ),
        )
        cur.execute(
            """
            INSERT INTO audit_log (entity_type, entity_id, action, actor, old_value, new_value, ip_address)
            VALUES ('company', %s, 'contact_route_imported', 'manual-import', NULL, %s, NULL)
            """,
            (
                company_id,
                Jsonb(
                    {
                        "route_source_url": norm.get("source_url"),
                        "route_source_label": norm.get("source_label"),
                        "route_source_type": norm.get("source_type"),
                        "route_entry_method": norm.get("route_entry_method"),
                        "contactability_bucket": rec.get("contactability_bucket"),
                        "best_route_type": rec.get("best_route_type"),
                    }
                ),
            ),
        )


def run(csv_path: str, *, write: bool, update_existing: bool) -> dict[str, object]:
    with open(csv_path, newline="", encoding="utf-8") as fh:
        parsed = list(csv.DictReader(fh))
    with get_conn() as conn:
        result = evaluate_batch(
            parsed, _make_resolver(conn), update_existing=update_existing
        )
        if write:
            for item in result["accepted"]:
                _persist(conn, item)
            conn.commit()
        else:
            conn.rollback()
    result["mode"] = "write" if write else "dry-run"
    return result


def main() -> None:
    args = _args()
    result = run(
        args.csv_path, write=args.write, update_existing=args.update_existing
    )
    if args.json:
        printable = {k: v for k, v in result.items() if k != "accepted"}
        print(json.dumps(printable, indent=2, default=str))
        return
    s = result["summary"]
    print(f"[{result['mode']}] {args.csv_path}")
    print(f"  processed={s['rows_processed']} accepted={s['accepted']} "
          f"rejected={s['rejected']} duplicates={s['duplicates']}")
    print(f"  -> ready_to_contact={s['moved_to_ready_to_contact']} "
          f"route_via_introducer={s['moved_to_route_via_introducer']} "
          f"still_research={s['still_research_required']}")
    for rej in result["rejected"][:20]:
        print(f"  REJECT row {rej['row']}: {', '.join(rej['errors'])}")
    if result["top_now_rm_ready"]:
        print("  Now RM-ready:")
        for opp in result["top_now_rm_ready"]:
            print(f"    - {opp['company_name']} [{opp['predicted_status']}] "
                  f"{opp['best_route']} ({opp['confidence']})")
    if not args.write:
        print("Dry-run only — no DB writes. Re-run with --write on staging to persist.")


if __name__ == "__main__":
    main()
