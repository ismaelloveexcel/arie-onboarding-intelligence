# ADR 0001 — DIFC and Gibraltar Ingestion Parked

## Status
Active

## Context
The Onboarding Intelligence Platform ingests new company incorporations from multiple jurisdictions. DIFC (Dubai International Financial Centre) and Gibraltar were identified as additional data sources. Before expanding to these sources, the Mauritius MNS ingestion needed to prove operationally stable — achieving consistent, clean pipeline runs without manual intervention.

## Decision
DIFC and Gibraltar ingestion is parked until Mauritius MNS achieves 10 consecutive clean runs without manual intervention or data quality issues.

## Consequences
- Leads from DIFC and Gibraltar are not currently captured.
- The pipeline codebase may have scaffolding for these sources that is not yet activated.
- Team members will not receive DIFC or Gibraltar leads until this decision is revisited.

## Revisit Trigger
Mauritius MNS achieves 10 consecutive clean runs. At that point, review this ADR and open a task to activate DIFC ingestion first, then Gibraltar separately.

## Amendment Log
(Append amendments here rather than modifying the sections above.)
