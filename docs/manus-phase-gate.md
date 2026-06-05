# Manus Phase Gate (A -> B)

## Purpose

This document defines the **permission boundary** for AI strategy work.
Manus must not proceed to design strategy (Phase B) until audit evidence (Phase A) passes this gate.

## Phase A: Audit Only

### Required output

Phase A must produce all of the following:

1. Architecture map (current reality, not aspirational)
2. Workflow map across Discover -> Qualify -> Contact -> Convert
3. Top 5 bottlenecks ranked by **impact x effort**
4. Evidence-backed contact intelligence gap assessment
5. UX hierarchy deficiencies tied to RM outcomes
6. Explicit "Do not build yet" list
7. Data/compliance risk map for proposed enrichment categories

### Phase A pass/fail rubric

Phase A is **PASS** only if:

- [ ] Top bottlenecks are ranked with justification
- [ ] Contact intelligence priority is evidence-backed
- [ ] Recommendations are specific and testable
- [ ] Compliance risks are explicit (not implied)
- [ ] "Do not build" list is concrete

If any item is missing, Phase A is FAIL and must be revised.

## Decision Gate (mandatory)

Before Phase B starts, a human reviewer must record:

- Phase A verdict: PASS / FAIL
- Confirmed top priority problem
- Non-goals for next phase
- Constraints for design output (compliance, scope, sequencing)

## Phase B: Strategy + Design

Phase B is allowed only after PASS and should produce:

- premium information architecture
- data/enrichment strategy
- contact intelligence system design
- phased roadmap with acceptance criteria

Phase B should not re-litigate Phase A findings; it should design within them.
