# Pilot Stabilization Phase

## Purpose

This phase starts **after Pilot Gates A-F are implemented and passing**.
Its goal is to validate that the system is not only structurally correct, but also
**behaviorally stable under normal usage**.

This is a safety window. No major product expansion should begin until this phase exits.

## Duration

- Minimum: **24 hours**
- Recommended: **24-48 hours** of real usage (internal or controlled pilot traffic)

## Freeze Rule

During stabilization:

- Allowed:
  - bug fixes
  - observability additions
  - incident mitigation
- Not allowed:
  - net-new enrichment domains
  - UI redesign projects
  - scoring model expansion beyond critical fixes

## Required Observations

### A) Runtime stability

- No recurring write failures on lead/introducer/update flows
- No unauthorized mutation access paths
- No broken save paths in RM workflow

### B) Behavior consistency

- Deterministic recompute behavior holds in practice
- No unexplained score drift for unchanged snapshots
- Why-output remains stable for equivalent inputs

### C) Operational signals

- Pipeline/enrichment jobs are not erroring at abnormal rates
- LEI ambiguous review queue grows predictably (no silent-link anomalies)
- URL safety behavior remains clean (no unsafe rendered links)

### D) RM workflow quality

- No high-friction workflow regressions
- Queue/actions remain responsive and reliable
- No repeated RM confusion around status/action state

## Exit Criteria (all required)

- [ ] Pilot Gates CI workflow is green on latest branch head
- [ ] No unresolved P0/P1 incidents during observation window
- [ ] Determinism/parity/audit checks pass in repeated runs
- [ ] Mutation/auth checks remain green
- [ ] LEI matching safety checks remain green
- [ ] Written stabilization summary published (date + owner + evidence links)

If any criterion fails, remain in stabilization until corrected and re-observed.

## Deliverable

Create a short stabilization report with:

- window start/end time
- incidents observed + fixes
- key metrics snapshot
- explicit Go/No-Go decision for strategy expansion (e.g., Manus Phase A)
