# Onboarding Intelligence Platform — Documentation Index

PLAN.md, BACKLOG.md, and SYSTEM_STATE.md remain at the repo root by convention — they are the active operational documents updated regularly. This folder holds longer-form reference documentation, runbooks, and architectural decision records that change less frequently.

- `architecture-overview.md` — System architecture, data sources, pipeline stages, routing rules, deployment topology
- `current-gate-status.md` — Single source of truth for Pilot Gate implementation and validation status
- `current-gate-status.yaml` — Canonical machine-readable gate and Manus transition state
- `stabilization-phase.md` — Required 24-48h behavior validation window after gates
- `stabilization-report-template.md` — Mechanical template and checklist for stabilization completion evidence
- `manus-phase-gate.md` — Phase A/Phase B rubric and decision boundary for Manus strategy work
- `minimal-compliance-contract.md` — MVP data/compliance handling contract for enrichment/contact intelligence
- `system-principles.md` — Long-term product/system principles to prevent operational drift
- `scripts/pilot_gates/gate_engine.py` — CI enforcement authority for governance state validation
- `runbooks/` — Operational runbooks for pipeline failures, data source debugging, deployment, and routing changes
- `decisions/` — Architectural decision records (ADRs) for significant technical or operational decisions
