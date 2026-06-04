"""Recompute the new priority/dimensional scores for every current lead.

Use after deploying PR 1 so existing rows show non-zero chips immediately.
Idempotent: re-runs simply overwrite the current row in place.

    python -m scripts.recompute_priority
"""

from __future__ import annotations

import logging
from datetime import date

from psycopg.types.json import Jsonb

from src.db import get_conn
from src.scoring import (
    SCORING_VERSION,
    build_reason_summary,
    build_why_reasons,
    calculate_arie_fit,
    calculate_freshness,
    calculate_keyword_score,
    calculate_score,
    compute_priority_score,
    derive_enrichment_tier,
    derive_lead_readiness,
    derive_reachability_status,
    load_lead_keywords,
    load_priority_weights,
)

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    updated = 0
    with get_conn() as conn:
        weights = load_priority_weights(conn)
        keywords = load_lead_keywords(conn)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, company_name, jurisdiction, entity_type, sic_codes, "
                "incorporation_date, website FROM companies"
            )
            companies = cur.fetchall()

        for (
            cid,
            name,
            jurisdiction,
            entity_type,
            sic_codes,
            inc_date,
            website,
        ) in companies:
            company = {
                "company_name": name,
                "jurisdiction": jurisdiction,
                "entity_type": entity_type,
                "sic_codes": sic_codes or [],
                "incorporation_date": inc_date,
            }

            with conn.cursor() as c:
                c.execute(
                    "SELECT registered_on FROM lei_records WHERE company_id = %s",
                    (cid,),
                )
                lei_row = c.fetchone()
            lei_data = None
            if lei_row and lei_row[0]:
                lei_data = {"days_since_registration": (date.today() - lei_row[0]).days}

            with conn.cursor() as c:
                c.execute(
                    "SELECT country_of_residence, ceased_on, email FROM company_pscs WHERE company_id = %s",
                    (cid,),
                )
                psc_raw = c.fetchall()
            pscs_data = [
                {"country_of_residence": r[0], "ceased_on": r[1]} for r in psc_raw
            ]
            psc_email_present = any((r[2] or "").strip() for r in psc_raw)

            with conn.cursor() as c:
                c.execute(
                    "SELECT nationality, resigned_on, email FROM company_officers WHERE company_id = %s",
                    (cid,),
                )
                off_raw = c.fetchall()
            officers_data = [
                {"nationality": r[0], "resigned_on": r[1]} for r in off_raw
            ]
            officer_email_present = any((r[2] or "").strip() for r in off_raw)
            active_officers = sum(1 for r in off_raw if r[1] is None)

            with conn.cursor() as c:
                c.execute(
                    "SELECT website, generic_email, linkedin_url FROM company_contacts WHERE company_id = %s",
                    (cid,),
                )
                cc_row = c.fetchone()
            cc_website = cc_row[0] if cc_row else None
            cc_email = cc_row[1] if cc_row else None
            cc_linkedin = cc_row[2] if cc_row else None

            with conn.cursor() as c:
                c.execute("SELECT status FROM rm_actions WHERE company_id = %s", (cid,))
                ra_row = c.fetchone()
            rm_status = ra_row[0] if ra_row else None

            score, codes, tier = calculate_score(
                company,
                lei=lei_data,
                pscs=pscs_data or None,
                officers=officers_data or None,
            )
            summary = build_reason_summary(codes)

            arie_fit, _ = calculate_arie_fit(company)
            freshness = calculate_freshness(inc_date)
            kw_score, _ = calculate_keyword_score(name or "", keywords)
            founder_q = 50
            cross_border = 0
            risk = 50

            has_website = bool((website or "").strip()) or bool(
                (cc_website or "").strip()
            )
            has_email = (
                officer_email_present
                or psc_email_present
                or bool((cc_email or "").strip())
            )
            has_linkedin = bool((cc_linkedin or "").strip())

            reach = derive_reachability_status(
                website=website or cc_website,
                has_email=has_email,
                linkedin_url=cc_linkedin,
            )
            readiness = derive_lead_readiness(
                arie_fit_score=arie_fit, reachability_status=reach, rm_status=rm_status
            )
            priority = compute_priority_score(
                arie_fit_score=arie_fit,
                founder_quality_score=founder_q,
                freshness_score=freshness,
                keyword_score=kw_score,
                cross_border_score=cross_border,
                risk_score=risk,
                weights=weights,
            )
            tier_enrichment = derive_enrichment_tier(priority)
            why = build_why_reasons(
                company=company,
                arie_fit_score=arie_fit,
                freshness_score=freshness,
                reachability_status=reach,
                has_website=has_website,
                has_email=has_email,
                has_linkedin=has_linkedin,
                lei=lei_data,
                officers_count=active_officers,
            )

            with conn.cursor() as c:
                c.execute(
                    "UPDATE lead_scores SET is_current = FALSE WHERE company_id = %s AND is_current = TRUE",
                    (cid,),
                )
                c.execute(
                    """
                    INSERT INTO lead_scores (
                        company_id, score, tier, reason_codes,
                        reason_summary, scoring_version, is_current,
                        arie_fit_score, keyword_score, freshness_score,
                        founder_quality_score, cross_border_score, risk_score,
                        priority_score, reachability_status, lead_readiness,
                        enrichment_tier, why_reasons
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, TRUE,
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        cid,
                        score,
                        tier,
                        codes,
                        summary,
                        SCORING_VERSION,
                        arie_fit,
                        kw_score,
                        freshness,
                        founder_q,
                        cross_border,
                        risk,
                        priority,
                        reach,
                        readiness,
                        tier_enrichment,
                        Jsonb(why),
                    ),
                )
            updated += 1

        conn.commit()

    logger.info("recompute_priority_done", extra={"updated": updated})
    print(f"Recomputed {updated} lead scores at version {SCORING_VERSION}")


if __name__ == "__main__":
    main()
