from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from src.config import ACTIVE_TERMINAL_STATUSES
from src.scoring import SIGNAL_DETAILS, build_reason_summary, calculate_score

logger = logging.getLogger(__name__)

SCORE_VERSION = "2026.2.0-shadow"
WEIGHTS_VERSION = "2026.2.0-w1"
RULES_VERSION = "2026.2.0-r1"
MODEL_VERSION = "deterministic-v1"

ALLOWED_TRIGGER_TYPES = ("nightly", "manual", "webhook", "backfill", "view")

_WEIGHTS = {
    "fit": 0.40,
    "founder_quality": 0.30,
    "keyword": 0.20,
    "risk": 0.10,
}

_KEYWORD_REASON_CODES = {"FINANCIAL_KEYWORD", "INTERNATIONAL_KEYWORD"}
_FOUNDER_DEFAULT_SCORE = 50
_RISK_DEFAULT_SCORE = 50


@dataclass(frozen=True)
class EvidenceSignal:
    signal: str
    component: str
    impact: int
    label: str
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "signal": self.signal,
            "component": self.component,
            "impact": self.impact,
            "label": self.label,
            "detail": self.detail,
        }


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _build_evidence(reason_codes: list[str]) -> list[EvidenceSignal]:
    evidence: list[EvidenceSignal] = []
    for code in sorted(reason_codes):
        details = SIGNAL_DETAILS.get(code) or {}
        points = details.get("points") or 0
        hidden = bool(details.get("hidden"))
        if hidden:
            continue
        component = "keyword" if code in _KEYWORD_REASON_CODES else "fit"
        evidence.append(
            EvidenceSignal(
                signal=code.lower(),
                component=component,
                impact=int(points),
                label=details.get("label") or code.replace("_", " ").title(),
                detail=details.get("why") or "",
            )
        )

    # Keep "founder/risk neutral" explicit in PR1a so score explainability is complete.
    evidence.append(
        EvidenceSignal(
            signal="founder_quality_neutral_baseline",
            component="founder_quality",
            impact=0,
            label="Founder quality baseline",
            detail="Founder score is neutral in PR1a until director intelligence lands.",
        )
    )
    evidence.append(
        EvidenceSignal(
            signal="risk_neutral_baseline",
            component="risk",
            impact=0,
            label="Risk baseline",
            detail="Risk score is neutral in PR1a pending paid risk signals.",
        )
    )
    return evidence


def _why_from_evidence(evidence: list[EvidenceSignal]) -> str:
    positive = [item for item in evidence if item.impact > 0]
    if not positive:
        return "Not yet scored."
    top = sorted(positive, key=lambda item: item.impact, reverse=True)[:4]
    return " | ".join(f"{item.label} (+{item.impact})" for item in top)


def compute_shadow_score(
    snapshot: dict[str, Any],
    *,
    scoring_version: str,
    weights_version: str,
    rules_version: str,
    model_version: str,
) -> dict[str, Any]:
    company = snapshot["company"]
    lei = snapshot.get("lei")
    pscs = snapshot.get("pscs") or None
    officers = snapshot.get("officers") or None

    fit_score, reason_codes, _tier = calculate_score(
        company,
        lei=lei,
        pscs=pscs,
        officers=officers,
        reference_date=snapshot["snapshot_timestamp"].date(),
    )

    keyword_score = min(
        100,
        sum(
            max(0, int((SIGNAL_DETAILS.get(code) or {}).get("points") or 0))
            for code in reason_codes
            if code in _KEYWORD_REASON_CODES
        ),
    )
    founder_quality_score = _FOUNDER_DEFAULT_SCORE
    risk_score = _RISK_DEFAULT_SCORE

    priority_score = round(
        _WEIGHTS["fit"] * fit_score
        + _WEIGHTS["founder_quality"] * founder_quality_score
        + _WEIGHTS["keyword"] * keyword_score
        + _WEIGHTS["risk"] * risk_score
    )
    priority_score = max(0, min(100, priority_score))

    evidence = _build_evidence(reason_codes)
    why_output = _why_from_evidence(evidence)

    serializable = {
        "snapshot": snapshot,
        "reason_codes": sorted(reason_codes),
        "evidence": [item.as_dict() for item in evidence],
        "score_version": scoring_version,
        "weights_version": weights_version,
        "rules_version": rules_version,
        "model_version": model_version,
    }
    evidence_hash = hashlib.sha256(
        json.dumps(
            serializable,
            sort_keys=True,
            separators=(",", ":"),
            default=_json_default,
        ).encode("utf-8")
    ).hexdigest()

    return {
        "fit_score": fit_score,
        "founder_quality_score": founder_quality_score,
        "keyword_score": keyword_score,
        "risk_score": risk_score,
        "priority_score": priority_score,
        "reason_codes": sorted(reason_codes),
        "reason_summary": build_reason_summary(reason_codes),
        "why_output": why_output,
        "evidence": [item.as_dict() for item in evidence],
        "evidence_hash": evidence_hash,
        "score_version": scoring_version,
        "weights_version": weights_version,
        "rules_version": rules_version,
        "model_version": model_version,
    }


def load_lead_snapshot(
    conn,
    lead_id: str | UUID,
    snapshot_timestamp: datetime,
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, company_name, jurisdiction, entity_type, sic_codes,
                   incorporation_date, updated_at
            FROM companies
            WHERE id = %s
            """,
            (str(lead_id),),
        )
        company_row = cur.fetchone()

    if not company_row:
        raise ValueError(f"Lead not found: {lead_id}")

    company = {
        "id": str(company_row[0]),
        "company_name": company_row[1],
        "jurisdiction": company_row[2],
        "entity_type": company_row[3],
        "sic_codes": company_row[4] or [],
        "incorporation_date": company_row[5],
        "updated_at": company_row[6],
    }

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT registered_on
            FROM lei_records
            WHERE company_id = %s
              AND first_seen <= %s
            ORDER BY registered_on DESC NULLS LAST
            LIMIT 1
            """,
            (str(lead_id), snapshot_timestamp),
        )
        lei_row = cur.fetchone()

    lei_data = None
    if lei_row and lei_row[0]:
        registration_date = lei_row[0]
        days_since = (snapshot_timestamp.date() - registration_date).days
        lei_data = {"days_since_registration": max(0, days_since)}

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT country_of_residence, ceased_on
            FROM company_pscs
            WHERE company_id = %s
              AND (fetched_at IS NULL OR fetched_at <= %s)
            ORDER BY fetched_at DESC NULLS LAST
            """,
            (str(lead_id), snapshot_timestamp),
        )
        psc_rows = cur.fetchall()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT nationality, resigned_on
            FROM company_officers
            WHERE company_id = %s
              AND (fetched_at IS NULL OR fetched_at <= %s)
            ORDER BY fetched_at DESC NULLS LAST
            """,
            (str(lead_id), snapshot_timestamp),
        )
        officer_rows = cur.fetchall()

    return {
        "snapshot_timestamp": snapshot_timestamp,
        "company": company,
        "lei": lei_data,
        "pscs": [
            {"country_of_residence": row[0], "ceased_on": row[1]} for row in psc_rows
        ],
        "officers": [{"nationality": row[0], "resigned_on": row[1]} for row in officer_rows],
    }


def _record_score_run(
    conn,
    *,
    lead_id: str,
    trigger_type: str,
    status: str,
    duration_ms: int,
    score_version: str,
    weights_version: str,
    rules_version: str,
    model_version: str,
    evidence_hash: str | None = None,
    snapshot_timestamp: datetime | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    idempotency_key: str | None = None,
    source_event_id: str | None = None,
) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO score_runs (
                lead_id, trigger_type, score_version, weights_version, rules_version,
                model_version, status, duration_ms, error_code, error_message,
                evidence_hash, snapshot_timestamp, idempotency_key, source_event_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING run_id
            """,
            (
                lead_id,
                trigger_type,
                score_version,
                weights_version,
                rules_version,
                model_version,
                status,
                duration_ms,
                error_code,
                error_message,
                evidence_hash,
                snapshot_timestamp,
                idempotency_key,
                source_event_id,
            ),
        )
        return str(cur.fetchone()[0])


def recompute_lead(
    conn,
    lead_id: str | UUID,
    *,
    trigger_type: str,
    scoring_version: str,
    weights_version: str,
    rules_version: str,
    model_version: str,
    snapshot_timestamp: datetime | None = None,
    idempotency_key: str | None = None,
    source_event_id: str | None = None,
) -> dict[str, Any]:
    if trigger_type not in ALLOWED_TRIGGER_TYPES:
        raise ValueError(f"Unsupported trigger_type={trigger_type!r}")

    lead_id_str = str(lead_id)
    start = time.time()
    snapshot_at = snapshot_timestamp or datetime.now(timezone.utc)

    if idempotency_key:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id
                FROM score_runs
                WHERE lead_id = %s
                  AND trigger_type = %s
                  AND idempotency_key = %s
                  AND status = 'success'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (lead_id_str, trigger_type, idempotency_key),
            )
            existing = cur.fetchone()
        if existing:
            run_id = _record_score_run(
                conn,
                lead_id=lead_id_str,
                trigger_type=trigger_type,
                status="skipped",
                duration_ms=0,
                score_version=scoring_version,
                weights_version=weights_version,
                rules_version=rules_version,
                model_version=model_version,
                error_code="idempotent_replay",
                error_message="Existing successful run found for idempotency key",
                idempotency_key=idempotency_key,
                source_event_id=source_event_id,
            )
            conn.commit()
            return {
                "status": "skipped",
                "run_id": run_id,
                "lead_id": lead_id_str,
                "snapshot_timestamp": snapshot_at.isoformat(),
            }

    try:
        snapshot = load_lead_snapshot(conn, lead_id_str, snapshot_at)
        score = compute_shadow_score(
            snapshot,
            scoring_version=scoring_version,
            weights_version=weights_version,
            rules_version=rules_version,
            model_version=model_version,
        )

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO score_versions (
                    score_version, weights_version, rules_version, model_version,
                    changed_by, change_reason
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (score_version, weights_version, rules_version, model_version) DO NOTHING
                """,
                (
                    score["score_version"],
                    score["weights_version"],
                    score["rules_version"],
                    score["model_version"],
                    "system",
                    "shadow baseline",
                ),
            )
            cur.execute(
                """
                UPDATE lead_signal_scores
                SET is_current = FALSE
                WHERE company_id = %s
                  AND is_current = TRUE
                """,
                (lead_id_str,),
            )
            cur.execute(
                """
                INSERT INTO lead_signal_scores (
                    company_id, snapshot_timestamp, score_state, fit_score,
                    founder_quality_score, keyword_score, risk_score, priority_score,
                    score_version, weights_version, rules_version, model_version, evidence_hash,
                    why_output, computed_at, is_current
                )
                VALUES (
                    %s, %s, 'scored', %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, NOW(), TRUE
                )
                RETURNING id
                """,
                (
                    lead_id_str,
                    snapshot_at,
                    score["fit_score"],
                    score["founder_quality_score"],
                    score["keyword_score"],
                    score["risk_score"],
                    score["priority_score"],
                    score["score_version"],
                    score["weights_version"],
                    score["rules_version"],
                    score["model_version"],
                    score["evidence_hash"],
                    score["why_output"],
                ),
            )
            score_id = str(cur.fetchone()[0])
            cur.execute(
                """
                INSERT INTO lead_score_evidence (
                    score_id, company_id, evidence_json, evidence_hash, why_output
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    score_id,
                    lead_id_str,
                    json.dumps(score["evidence"], sort_keys=True),
                    score["evidence_hash"],
                    score["why_output"],
                ),
            )
            cur.execute(
                """
                UPDATE companies
                SET score_state = 'scored',
                    score_state_reason = NULL,
                    score_state_updated_at = NOW()
                WHERE id = %s
                """,
                (lead_id_str,),
            )

        duration_ms = int((time.time() - start) * 1000)
        run_id = _record_score_run(
            conn,
            lead_id=lead_id_str,
            trigger_type=trigger_type,
            status="success",
            duration_ms=duration_ms,
            score_version=score["score_version"],
            weights_version=score["weights_version"],
            rules_version=score["rules_version"],
            model_version=score["model_version"],
            evidence_hash=score["evidence_hash"],
            snapshot_timestamp=snapshot_at,
            idempotency_key=idempotency_key,
            source_event_id=source_event_id,
        )
        conn.commit()
        return {
            "status": "success",
            "run_id": run_id,
            "lead_id": lead_id_str,
            "score_id": score_id,
            "snapshot_timestamp": snapshot_at.isoformat(),
            "score_version": score["score_version"],
            "weights_version": score["weights_version"],
            "rules_version": score["rules_version"],
            "model_version": score["model_version"],
            "evidence_hash": score["evidence_hash"],
            "priority_score": score["priority_score"],
        }
    except Exception as exc:
        conn.rollback()
        duration_ms = int((time.time() - start) * 1000)
        message = str(exc)[:1000]
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE companies
                SET score_state = 'failed',
                    score_state_reason = %s,
                    score_state_updated_at = NOW()
                WHERE id = %s
                """,
                (message, lead_id_str),
            )
        run_id = _record_score_run(
            conn,
            lead_id=lead_id_str,
            trigger_type=trigger_type,
            status="failure",
            duration_ms=duration_ms,
            score_version=scoring_version,
            weights_version=weights_version,
            rules_version=rules_version,
            model_version=model_version,
            error_code=exc.__class__.__name__,
            error_message=message,
            snapshot_timestamp=snapshot_at,
            idempotency_key=idempotency_key,
            source_event_id=source_event_id,
        )
        conn.commit()
        logger.exception(
            "shadow_recompute_failed",
            extra={"lead_id": lead_id_str, "trigger_type": trigger_type, "run_id": run_id},
        )
        raise


def select_active_lead_ids(
    conn,
    *,
    stale_days: int,
    limit: int,
) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.id
            FROM companies c
            LEFT JOIN rm_actions ra ON ra.company_id = c.id
            LEFT JOIN queue_snapshot qs ON qs.canonical_company_id = c.id
            WHERE
                (qs.canonical_company_id IS NOT NULL OR COALESCE(ra.assigned_to, '') <> '')
                AND COALESCE(ra.status, 'new') <> ALL(%s)
                AND c.updated_at >= NOW() - (%s || ' days')::interval
                AND (
                    c.score_state <> 'scored'
                    OR c.score_state_updated_at IS NULL
                    OR c.score_state_updated_at < c.updated_at
                )
            ORDER BY c.updated_at DESC
            LIMIT %s
            """,
            (list(ACTIVE_TERMINAL_STATUSES), stale_days, limit),
        )
        return [str(row[0]) for row in cur.fetchall()]


def backfill_active_shadow_scores(
    conn,
    *,
    stale_days: int,
    batch_size: int,
    max_batches: int,
    lock_timeout_ms: int,
) -> dict[str, Any]:
    total_scanned = 0
    total_scored = 0
    total_failed = 0
    batches = 0

    for _ in range(max_batches):
        with conn.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout = %s", (f"{lock_timeout_ms}ms",))
        lead_ids = select_active_lead_ids(conn, stale_days=stale_days, limit=batch_size)
        if not lead_ids:
            break
        batches += 1
        for lead_id in lead_ids:
            total_scanned += 1
            try:
                recompute_lead(
                    conn,
                    lead_id,
                    trigger_type="backfill",
                    scoring_version=SCORE_VERSION,
                    weights_version=WEIGHTS_VERSION,
                    rules_version=RULES_VERSION,
                    model_version=MODEL_VERSION,
                )
                total_scored += 1
            except Exception:
                total_failed += 1

    return {
        "batches_processed": batches,
        "scanned": total_scanned,
        "scored": total_scored,
        "failed": total_failed,
    }
