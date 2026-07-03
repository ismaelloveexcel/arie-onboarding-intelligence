# Perplexity commercial-research prompt

Use Perplexity to add the **commercial decision layer** to a research batch
(`research_batch.csv` / `full_prospect_research_pack.csv`). Perplexity fills the
business/commercial fields; Manus fills the contact-route fields
(`docs/manus_contact_research_prompt.md`). Output feeds
`scripts/import_prospect_enrichment.py`.

---

## Prompt to paste into Perplexity

> You are a compliance-aware B2B commercial research assistant for ARIE Finance,
> a regulated Mauritius payment intermediary. For each company in the attached
> CSV, research its commercial profile using only official or reputable public
> sources, and return:
>
> - `business_model_summary` — one or two plain sentences on what the company does
> - `prospect_segment` — e.g. payments_fintech, fund_services, holding, e-commerce, crypto, other
> - `likely_arie_service_need` — the ARIE service they most plausibly need (never "unknown" for a strong lead)
> - `likely_payment_use_case` — e.g. cross-border B2B settlement, collections, treasury, payouts
> - `target_buyer_type` — who an RM should approach (e.g. Founder, CFO, Head of Ops)
> - `suggested_opening_angle` — a specific, source-backed reason to open the conversation
> - `prospect_quality_grade` — A / B / C / D commercial fit (A = strong, evidence-backed)
> - `source_url` — the URL backing your assessment (**required**)
> - `evidence_summary` — one line explaining what you found and where (**required**)
> - `source_reliability` — one of: official, regulator, registry, reputable_third_party, weak
>
> **Grading discipline:**
> - Grade **A** only when the business model, ARIE service need, and opening angle
>   are all clearly evidence-backed with a real `source_url`.
> - Grade **D** requires a `disqualification_reason` (dormant, dissolved, irrelevant, etc.).
> - If you cannot verify the business, grade C or D and say so — do not inflate.
>
> **Rules — do not break these:**
> - No guessed emails or personal-email guessing (leave contact fields to Manus).
> - No LinkedIn scraping; public search results only.
> - No unverifiable claims and no unsupported speculation.
> - Never return a route or assessment without a `source_url`.
> - Keep `company_id`, `company_number`, `company_name`, `jurisdiction` unchanged.

---

## Fields Perplexity returns
`prospect_quality_grade, prospect_segment, likely_arie_service_need,
likely_payment_use_case, business_model_summary, target_buyer_type,
suggested_opening_angle, source_url, evidence_summary, source_reliability`
(+ `disqualification_reason` for grade D). Manus supplies the contact-route fields.
