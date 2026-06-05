# System Principles

These principles are the long-term guardrails for product and engineering decisions.

## 1) Lead quality > data volume

We optimize for actionable, trustworthy leads over raw record count.
Adding more entities without decision value is not progress.

## 2) Contactability > enrichment breadth

A lead that can be contacted is more valuable than a deeply enriched lead with no route to outreach.
Contact intelligence and verification are priority surfaces.

## 3) Deterministic scoring > AI inference

Scores must be reproducible and auditable.
Non-deterministic shortcuts that reduce trust are unacceptable for core ranking decisions.

## 4) Explainability > black-box accuracy

Every major decision output should be traceable to evidence.
If we cannot explain why a lead is prioritized, the system is not production-trustworthy.

## 5) RM workflow > engineering elegance

The product exists to accelerate RM decisions:
Discover -> Qualify -> Contact -> Convert.
Architecture choices must serve operational workflow outcomes first.

## Practical decision test

Any major feature should pass:

- Does it improve RM action speed or quality?
- Is it auditable and explainable?
- Does it preserve deterministic behavior where required?
- Does it stay within compliance constraints?

If not, it should not be prioritized.
