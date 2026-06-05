# Minimal Compliance Contract (MVP)

## Purpose

Define baseline data-handling constraints for lead intelligence expansion.
This is not legal advice; it is an engineering operating contract to reduce compliance drift.

## Scope

Applies to:

- company enrichment
- contact discovery
- decision-maker intelligence
- scoring inputs derived from third-party/public sources

## Data Handling Rules

### 1) Source attribution is mandatory

Every enriched/contact field must have:

- `source`
- `discovered_at`
- `confidence_score`
- `verification_state` (where applicable)

Fields without provenance should not be treated as trusted or high-confidence.

### 2) Allowed data classes (MVP)

- company-level public registry data
- business contact points discovered from lawful/public business sources
- role/professional metadata tied to business context

### 3) Prohibited for MVP

- sensitive personal data categories
- private credentials/secrets scraping
- bypassing platform controls that violate source terms

### 4) Retention and deletion

- Define retention windows for discovered contacts and enrichment artifacts
- Support deletion/suppression workflows for records requiring removal
- Ensure source references and audit events remain traceable

### 5) Scoring and explainability

- No black-box score contributions without explainable provenance
- Scoring inputs must remain reproducible from stored state
- Why-output must be traceable to stored evidence objects

### 6) Outbound use controls

- Contactability signals must be confidence-scored
- Unverified contacts should be clearly marked and handled separately
- Jurisdiction-specific outreach constraints must be honored operationally

## Vendor/Source Onboarding Checklist (minimum)

- [ ] Terms of use reviewed for intended access pattern
- [ ] Data class and jurisdiction implications identified
- [ ] Attribution fields mapped into schema
- [ ] Confidence/verification behavior defined
- [ ] Retention/deletion behavior documented

## Change Rule

Any new enrichment/contact source is blocked until this checklist is completed.
