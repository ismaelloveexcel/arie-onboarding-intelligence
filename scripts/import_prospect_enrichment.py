"""Import externally-researched RM/commercial enrichment (Manus + Perplexity).

Dry-run by default — validates every row, previews the RM readiness outcome, and
rolls back. `--write` persists to the prospect_enrichment table (staging only).
Production writes are blocked unless explicitly allowed.

    python -m scripts.import_prospect_enrichment enriched.csv               # dry-run
    python -m scripts.import_prospect_enrichment enriched.csv --write       # staging
    python -m scripts.import_prospect_enrichment enriched.csv --write --allow-production
"""

from __future__ import annotations

import argparse
import csv
import json
import os

from psycopg.types.json import Jsonb

from src.db import get_conn
from src.prospect_enrichment import (
    HOLD,
    READY_TO_WORK,
    REJECT,
    RESEARCH_ROUTE,
    validate_enrichment_row,
)


def _args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("csv_path")
    p.add_argument("--write", action="store_true")
    p.add_argument("--update-existing", action="store_true")
    p.add_argument("--allow-production", action="store_true")
    p.add_argument("--json", action="store_true")
    return p.parse_args(argv)


def _company_key(norm) -> str:
    return str(norm.get("company_id") or norm.get("company_number") or "").lower()


def write_blocked_reason(*, allow_production: bool) -> str | None:
    """Return a reason string if writing must be blocked, else None."""
    if os.environ.get("APP_ENV", "").lower() == "production" and not allow_production:
        return "APP_ENV=production — refusing to write without --allow-production"
    return None


def evaluate_enrichment_batch(rows, resolver, *, update_existing: bool = False) -> dict:
    """Pure evaluation: validate, dedupe, resolve, bucket. No DB, no I/O.

    resolver(normalized) -> {company_id, has_existing_enrichment} or None.
    """
    accepted, rejected, duplicates = [], [], []
    seen: set[str] = set()

    for i, raw in enumerate(rows):
        v = validate_enrichment_row(raw)
        if not v["ok"]:
            rejected.append({"row": i + 1, "errors": v["errors"]})
            continue
        key = _company_key(v["normalized"])
        if key and key in seen:
            duplicates.append({"row": i + 1, "reason": "Duplicate within file"})
            continue
        seen.add(key)
        company = resolver(v["normalized"])
        if company is None:
            rejected.append({"row": i + 1, "errors": ["Company not found"]})
            continue
        if company.get("has_existing_enrichment") and not update_existing:
            duplicates.append({"row": i + 1, "reason": "Company already enriched"})
            continue
        accepted.append({
            "row": i + 1, "company": company, "normalized": v["normalized"],
            "bucket": v["bucket"], "ready_to_work": v["ready_to_work"],
            "raw": raw,
        })

    def _count(bucket):
        return sum(1 for a in accepted if a["bucket"] == bucket)

    missing_evidence = sum(1 for r in rows if not (r.get("evidence_summary") or "").strip())
    weak_unusable = sum(
        1 for r in rows
        if (r.get("source_reliability") or "").strip().lower() == "weak"
        or (r.get("route_quality") or "").strip().lower() == "unusable"
    )

    return {
        "accepted": accepted,
        "rejected": rejected,
        "duplicates": duplicates,
        "summary": {
            "rows_processed": len(rows),
            "accepted": len(accepted),
            "rejected": len(rejected),
            "duplicates": len(duplicates),
            "ready_to_work": _count(READY_TO_WORK),
            "research_route": _count(RESEARCH_ROUTE),
            "hold": _count(HOLD),
            "reject": _count(REJECT),
            "missing_mandatory_evidence": missing_evidence,
            "weak_or_unusable_routes": weak_unusable,
        },
    }


# --------------------------------------------------------------------------- DB


def _make_resolver(conn):
    def resolver(norm):
        with conn.cursor() as cur:
            if norm.get("company_id"):
                cur.execute("SELECT id FROM companies WHERE id = %s", (norm["company_id"],))
            elif norm.get("company_number"):
                cur.execute("SELECT id FROM companies WHERE source_ref = %s", (norm["company_number"],))
            else:
                return None
            found = cur.fetchone()
            if not found:
                return None
            company_id = str(found[0])
            cur.execute("SELECT 1 FROM prospect_enrichment WHERE company_id = %s", (company_id,))
            existing = cur.fetchone() is not None
        return {"company_id": company_id, "has_existing_enrichment": existing}
    return resolver


def _to_bool(value):
    return str(value or "").strip().lower() == "true"


def _persist(conn, item) -> None:
    n = item["normalized"]
    cid = item["company"]["company_id"]
    cols = {
        "prospect_quality_grade": n.get("prospect_quality_grade"),
        "prospect_segment": n.get("prospect_segment"),
        "likely_arie_service_need": n.get("likely_arie_service_need"),
        "likely_payment_use_case": n.get("likely_payment_use_case"),
        "business_model_summary": n.get("business_model_summary"),
        "target_buyer_type": n.get("target_buyer_type"),
        "suggested_opening_angle": n.get("suggested_opening_angle"),
        "best_contact_route": n.get("best_contact_route"),
        "route_quality": n.get("route_quality"),
        "source_reliability": n.get("source_reliability"),
        "research_status": n.get("research_status") or "researched",
        "next_rm_action": n.get("next_rm_action"),
        "disqualification_reason": n.get("disqualification_reason"),
        "route_entry_method": n.get("route_entry_method") or "import",
        "checked_by": n.get("checked_by"),
        "rm_owner": n.get("rm_owner"),
        "rm_status": n.get("rm_status") or "not_started",
        "rm_outcome_notes": n.get("rm_outcome_notes"),
        "lost_reason": n.get("lost_reason"),
        "source_url": n.get("source_url"),
        "source_label": n.get("source_label"),
        "source_type": n.get("source_type"),
        "evidence_summary": n.get("evidence_summary"),
    }
    fields = list(cols.keys()) + [
        "company_id", "management_shortlist_flag", "ready_to_work",
        "readiness_bucket", "checked_at", "raw_payload",
    ]
    values = list(cols.values()) + [
        cid, _to_bool(n.get("management_shortlist_flag")), item["ready_to_work"],
        item["bucket"], None, Jsonb(item["raw"]),
    ]
    placeholders = ", ".join(["%s"] * len(fields))
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in fields if c != "company_id")
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO prospect_enrichment ({', '.join(fields)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT (company_id) DO UPDATE SET {updates}, updated_at = NOW()",
            values,
        )


def run(csv_path, *, write, update_existing, allow_production) -> dict:
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    if write:
        blocked = write_blocked_reason(allow_production=allow_production)
        if blocked:
            raise SystemExit(f"WRITE BLOCKED: {blocked}")
    with get_conn() as conn:
        result = evaluate_enrichment_batch(rows, _make_resolver(conn), update_existing=update_existing)
        if write:
            for item in result["accepted"]:
                _persist(conn, item)
            conn.commit()
        else:
            conn.rollback()
    result["mode"] = "write" if write else "dry-run"
    return result


def main() -> None:
    a = _args()
    result = run(a.csv_path, write=a.write, update_existing=a.update_existing,
                 allow_production=a.allow_production)
    if a.json:
        print(json.dumps({k: v for k, v in result.items() if k != "accepted"}, indent=2, default=str))
        return
    s = result["summary"]
    print(f"[{result['mode']}] {a.csv_path}")
    print(f"  processed={s['rows_processed']} accepted={s['accepted']} "
          f"rejected={s['rejected']} duplicates={s['duplicates']}")
    print(f"  -> ready_to_work={s['ready_to_work']} research_route={s['research_route']} "
          f"hold={s['hold']} reject={s['reject']}")
    print(f"  missing_mandatory_evidence={s['missing_mandatory_evidence']} "
          f"weak_or_unusable_routes={s['weak_or_unusable_routes']}")
    for rej in result["rejected"][:25]:
        print(f"  REJECT row {rej['row']}: {', '.join(rej['errors'])}")
    for dup in result["duplicates"][:25]:
        print(f"  DUPLICATE row {dup['row']}: {dup['reason']}")
    if not a.write:
        print("Dry-run only — no data written. Re-run with --write on staging to persist.")


if __name__ == "__main__":
    main()
