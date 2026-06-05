# Arie Lead Intelligence Platform — Documentation Index

## Start here

- `architecture-overview.md` — What the platform is, how a lead moves through the system, data sources, RM workflow, and operational dependencies. **Read this first.**
- `product-roadmap.md` — What is live now, what is planned next, what is parked and why.
- `known-limitations.md` — What the platform intentionally does not do yet. Prevents treating deferrals as bugs.

- `pilot-monitoring-spec.md` — What to observe, measure, and record in the first 7 days of live RM usage.

## Governance and operations

- `system-principles.md` — Long-term product and engineering guardrails
- `current-gate-status.md` — Single source of truth for Pilot Gate implementation and validation status
- `current-gate-status.yaml` — Canonical machine-readable gate and Manus transition state
- `minimal-compliance-contract.md` — MVP data-handling constraints for enrichment and contact intelligence
- `stabilization-phase.md` — Required 24–48h behaviour validation window after gates
- `stabilization-report-template.md` — Mechanical checklist for stabilisation completion evidence
- `manus-phase-gate.md` — Phase A/Phase B rubric and decision boundary for strategy review
- `runbooks/` — Operational runbooks: pipeline failures, data source debugging, deployment, routing changes
- `decisions/` — Architectural decision records (ADRs) for significant technical or product decisions

## Engineering reference

- `scripts/pilot_gates/gate_engine.py` — CI enforcement authority for governance state validation
