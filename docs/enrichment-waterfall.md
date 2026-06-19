# Explainable Enrichment Waterfall

## Guardrails

- Preserve the current Lead Fit Score and deterministic queue logic.
- Record evidence before deriving contact readiness or an introducer route.
- Keep enrichment read-only. Do not automate outreach or create CRM workflows.
- Show RMs the value, source, confidence, and freshness without exposing pipeline detail.

## Provenance Model

The current `company_contacts` row is suitable for today's company-level research bundle: website, generic email, contact form, and company LinkedIn share `source`, `confidence`, `verified_at`, and `checked_by`.

Person-level contacts and derived introducer hints need field-level provenance. Add one append-only table rather than adding repeated metadata columns to every source table:

| Field | Purpose |
|---|---|
| `id`, `company_id` | Observation identity and prospect link |
| `subject_type`, `subject_id` | `company`, `officer`, `psc`, or `introducer_route` |
| `field_name`, `field_value` | Observed field and normalized value |
| `source_name`, `source_url` | Human-readable origin and evidence link |
| `confidence` | `Low`, `Medium`, or `High` |
| `checked_at`, `checked_by` | Freshness and accountable researcher/process |
| `is_current` | Current value while preserving superseded evidence |

Suggested name: `contact_observations`. Keep `company_contacts` as the current-value projection until migration is justified.

## Deterministic Confidence Rules

| Confidence | Rule |
|---|---|
| High | Exact legal identifier or value confirmed on the entity's official site/register; internal introducer match uses an exact normalized entity or registered-office identifier. |
| Medium | Value appears on a credible register, official social profile, or two independent public sources; derived registered-office/CSP hints are Medium until confirmed. |
| Low | Search-result discovery, name/domain inference, or a single indirect source that still needs verification. |

Confidence never changes the Lead Fit Score. Conflicting values remain separate observations and are flagged for review.

## Waterfall

| Order | Source and purpose | Proposed fields | UI placement | Priority |
|---|---|---|---|---|
| 1 | Companies House / MNS CBRIS / OpenCorporates: legal entity verification | `legal_name`, `registration_number`, `jurisdiction`, `entity_type`, `entity_status`, `registered_address`, `verified_at`, provenance | Company Details; verification badge and source link | Today: confirm existing coverage. This Week: normalize source labels. |
| 2 | GLEIF: LEI and group context | `lei`, `lei_status`, `legal_parent_lei`, `ultimate_parent_lei`, `managing_lou`, `last_seen`, provenance | Existing LEI panel; compact group-context row | Today: retain current LEI lookup. This Week: map parent relationships when present. |
| 3 | Brave Search or similar: website/contact discovery | `website`, `domain`, `contact_form_url`, evidence URL, provenance | Contact Research; prefill as an unconfirmed observation | This Week: discovery adapter and review queue. |
| 4 | Hunter.io: generic company email | `generic_email`, `email_type`, `domain_match`, provenance | Contact Research; never mark Ready to Contact until deterministic checks pass | Later: vendor evaluation and cost controls. |
| 5 | FSC / FCA / DFSA / ADGM registers: regulatory context | `regulator`, `licence_number`, `regulated_status`, `permissions_summary`, provenance | Regulatory Context below Company Details | This Week: source mapping. Later: adapters by register. |
| 6 | OpenSanctions: basic risk context | `dataset`, `match_status`, `matched_name`, `match_basis`, provenance | Restricted Risk Context panel; review-only, never a sales score | Later: compliance-approved matching thresholds and retention. |
| 7 | Registered-office clustering: CSP/management-company route | `normalized_address`, `cluster_size`, `candidate_csp`, `match_basis`, provenance | Introducer route callout and dedicated queue filter | This Week: deterministic address normalization and cluster report. |
| 8 | Internal introducer list matching | `introducer_id`, `match_type`, `matched_value`, provenance | Introducer callout with link to introducer detail | This Week: exact company/address/domain matching. Later: reviewed fuzzy matching. |

## Field Coverage

| Contact field | Current state | Next implementation |
|---|---|---|
| Company website, generic email, contact form, company LinkedIn | Supported as one reviewed company-contact bundle with source, confidence, last checked, checked by | Move to field observations only when multiple/conflicting values become common. |
| Officer/PSC email | Editable, but no field provenance | Store through `contact_observations`; keep registry person rows source-owned. |
| Officer/PSC LinkedIn | Not captured | Add reviewed observation UI after the field-level table exists. Do not scrape LinkedIn. |
| Introducer route hint | Deterministic rule displayed with its basis and Medium confidence | Persist as an observation only when matched to a registered office or internal introducer. |

## Delivery Sequence

**Today:** enforce complete provenance for manual company contact research; label deterministic introducer hints; retain existing entity and LEI verification.

**This Week:** add `contact_observations`, registered-office normalization/clustering, exact internal introducer matching, and reviewed search discovery. Add regulatory source mapping without changing scoring.

**Later:** evaluate generic-email and sanctions providers with compliance, retention, rate-limit, and cost controls. Add person-level contact capture only through reviewed observations.
