# Manual contact-route import (free / search-only)

The single biggest lever on lead quality right now is **populating contact
routes**. The route engine is sound, but most leads have no stored website,
generic email, contact form, or introducer/CSP link — so they can only be
classified as *Research First* or *No Compliant Route*. Filling these fields
turns leads into *Contact Now* / *Route via Introducer*.

This is a deterministic, manual/search-only path. **No scraping of LinkedIn or
closed platforms. No guessed personal emails. No paid APIs.** Use only
first-party or public sources (company website, official registry, FSC register,
regulator listings).

## Template
Fill in `docs/manual_contact_routes_template.csv`. Columns:

| Column | Meaning | Maps to |
|---|---|---|
| `company_id` | UUID if known (preferred) | `companies.id` |
| `company_name` / `jurisdiction` | used to match if no id | `companies` |
| `website` | official company website | `company_contacts.website` |
| `generic_email` | company-level email (info@, contact@) — **never a guessed personal address** | `company_contacts.generic_email` |
| `contact_form_url` | URL of the company contact form | `company_contacts.contact_form_url` |
| `linkedin_company_url` | company page only, found by manual search | `company_contacts.linkedin_url` |
| `introducer_or_csp` | name of the introducer/CSP/administrator route | matched to `introducers` |
| `source_url` | where the evidence came from | `route_recommendations.route_source_url` |
| `source_label` | short source name (e.g. "FSC register") | `route_recommendations.route_source_label` |
| `route_entry_method` | `manual` or `import` | `route_recommendations.route_entry_method` |
| `confidence` | high / medium / low | `route_recommendations.confidence` |
| `evidence_summary` | one line of provenance | `route_recommendations.evidence_summary` |
| `checked_by` / `checked_at` | RM and date | `company_contacts.verified_at` / `checked_by` |

## How it is consumed
After contact fields are stored on `company_contacts`, re-running the route
operator re-classifies those leads:

```
python -m scripts.route_intelligence --top-high-fit 100          # dry-run preview
python -m scripts.route_intelligence --top-high-fit 100 --write  # persist (staging first)
```

A lead with a stored generic email or contact form becomes **ready_to_contact**;
a matched introducer/CSP becomes **route_via_introducer**.

## Status
A dedicated importer for `company_contacts` is **not yet built on this branch**
(the existing CSV importers cover Mauritius registered offices and introducers).
Building `scripts/import_contact_routes.py` (dry-run default, `--write` to persist,
matching the route-operator safety pattern) is the fastest next step to make this
template self-serve.
