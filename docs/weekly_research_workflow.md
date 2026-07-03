# Weekly lead-research workflow

The repeatable loop that turns hundreds of scored leads into a short list of
RM-ready prospects. Free / manual / search-only — no scraping, no paid APIs.

## Phase 1 — RM/commercial enrichment (Manus + Perplexity)

The research batch now carries blank RM/commercial columns (see
`docs/prospect_enrichment_import_template.csv`). Two assistants fill them:

- **Manus** → contact-route fields (website, contact page/form, generic email,
  introducer/CSP, `best_contact_route`, `route_quality`, `source_reliability`,
  `source_url`, `evidence_summary`). Prompt: `docs/manus_contact_research_prompt.md`.
- **Perplexity** → commercial fields (`business_model_summary`, `prospect_segment`,
  `likely_arie_service_need`, `likely_payment_use_case`, `target_buyer_type`,
  `suggested_opening_angle`, `prospect_quality_grade`, `source_url`,
  `evidence_summary`, `source_reliability`). Prompt:
  `docs/perplexity_commercial_research_prompt.md`.

Import the combined CSV (dry-run by default; writes blocked on production):
```
python -m scripts.import_prospect_enrichment enriched.csv            # dry-run
python -m scripts.import_prospect_enrichment enriched.csv --write    # staging only
```
A row only becomes **Ready to Work** when it is grade A with a non-weak source,
a usable named route, evidence + source URL, a known ARIE service need, a business
model summary, an opening angle, and a `contact_now` / `route_via_introducer`
next action. Everything else falls into Research Route, Hold, or Reject. Stored
in the dedicated `prospect_enrichment` table (never the core company tables).

## The loop (contact-route only — legacy/simple path)

1. **Export a research batch**
   ```
   python -m scripts.export_research_batch --limit 50
   ```
   Produces `research_batch.csv` — the best HIGH/MEDIUM leads that are not yet
   RM-ready and lack a contact route, with why-this-lead / why-now / registry /
   officers / PSC context and a per-row research instruction.

2. **Research with Manus (or by hand)**
   Upload `research_batch.csv` to Manus using
   `docs/manus_contact_research_prompt.md`. Manus returns an enriched CSV with
   website / contact form / generic email / introducer-CSP routes, each with a
   source URL and evidence. Compliant sources only.

3. **Dry-run the import**
   ```
   python -m scripts.import_contact_routes enriched.csv
   ```
   Validates every row, shows accepted / rejected / duplicates and the predicted
   readiness movement. **No DB writes.** Fix any rejected rows in the CSV and
   re-run until clean.

4. **Import into staging**
   ```
   python -m scripts.import_contact_routes enriched.csv --write
   ```
   Run against a **staging** database (never production). Persists the contact
   routes and refreshes route recommendations. Use `--update-existing` to refresh
   companies that already have a route.

5. **Re-run readiness + audit**
   ```
   python -m scripts.route_intelligence --top-high-fit 100        # recompute
   python -m scripts.audit_lead_quality --limit 200               # measure
   ```

6. **Review Top Opportunities**
   Open the dashboard → **Top Opportunities** and **Client Acquisition**. The
   leads that gained a compliant route now appear as Contact Now / Route via
   Introducer; open each lead to see the Action Recommendation, evidence chips,
   and next action.

## Safety / compliance
- Personal or guessed emails are rejected and never count as ready_to_contact.
- Every accepted route carries a source URL and evidence.
- Dry-run is the default; writes require `--write` and should target staging.
- Scoring is never changed — this loop only improves routes and readiness.
