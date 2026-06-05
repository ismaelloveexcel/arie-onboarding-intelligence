# Product Roadmap — Arie Lead Intelligence Platform

Last updated: 2026-06-05

> This document tracks what the platform is, what it will become, and why.
> Every item includes the problem it solves and the expected outcome — not just the feature name.
> Priorities are reviewed after each deployment cycle based on observed RM behaviour.

---

## Now — Currently Live

### Lead Intelligence Queue
The core RM workflow surface. Ranked, filtered list of scored leads with primary signal chips, follow-up indicators, and Excel export.

### Lead Detail — Decision Interface
Single-page decision surface: why contact now, key contacts, activity timeline, outreach briefing, score breakdown, RM actions, audit trail.

### Deterministic Scoring Engine
Rule-based scoring across 15 signals. Fully explainable per-signal breakdown. Versioned and auditable.

### Data Ingestion — UK (Companies House)
Nightly automated ingestion of UK incorporations, officers, PSCs, and SIC codes.

### Data Ingestion — Mauritius (MNS)
Nightly automated ingestion of Mauritius GBC and AC entity formations.

### LEI Backfill — GLEIF
Nightly matching of Legal Entity Identifier registrations to company records. Fresh LEI is one of the strongest scoring signals.

### Introducer Management (Manual)
CSV upload, contact records, status tracking, and assignment for corporate service providers and introducers of business.

### RM Productivity Dashboard
Team-level contact rate, conversion rate, overdue follow-ups, and per-RM breakdown.

### Governance Infrastructure
Pilot readiness gates A–F, CI enforcement, write authorization, audit trail, deterministic scoring validation.

### Excel Export
Filtered queue export to Excel with all business-relevant columns. Respects active filters and sort order.

### Auto Follow-Up Date
When a contacted date is entered and no follow-up date exists, the system automatically proposes +7 days. User-entered dates are never overwritten.

---

## Next — Approved for Development

### RM Scroll Position Preservation
**Problem:** When an RM opens a lead and returns to the queue, the browser reloads from the top. Queue position is lost.
**Why it matters:** RMs processing 20–30 leads in a session lose their place after every lead detail visit. This interrupts flow and reduces the number of leads reviewed per session.
**Expected outcome:** RMs can process larger batches without friction. Session depth increases.
**Status:** Next — high impact, low complexity.

---

### Introducer Performance Analytics
**Problem:** There is no way to measure which introducers bring the most clients, which relationships are active versus dormant, or how much revenue each introducer channel generates.
**Why it matters:** Arie's introducer network is a primary client acquisition channel. Without performance data, relationship investment is based on intuition rather than evidence.
**Expected outcome:** Management can identify top-performing introducers, prioritise relationship time accordingly, and detect declining introducer activity early.
**Status:** Next — requires introducer-to-lead linkage model (see Known Limitations).

---

### Contact Enrichment — Decision Pending
**Problem:** Key Contacts for most leads are empty at the time of RM review. RMs must manually research decision-maker contact details before outreach.
**Why it matters:** The gap between "identified lead" and "first outreach" is primarily contact research time. Reducing this is the highest-impact productivity improvement available.
**Expected outcome:** RMs arrive at a lead with a named decision-maker, email, and LinkedIn profile already populated. Time to first outreach reduces significantly.
**Status:** Decision pending — provider evaluation and GDPR compliance checklist must be completed first. See `docs/decisions/contact-enrichment-providers.md`.

---

### Bulk Lead Processing
**Problem:** RMs cannot assign, update status, or triage multiple leads simultaneously. Every action requires opening individual lead detail pages.
**Why it matters:** At volume (50+ new leads per week), individual processing is a bottleneck. Bulk triage enables efficient weekly prioritisation sessions.
**Expected outcome:** RMs can triage a full week's leads in a single session rather than processing one by one.
**Status:** Next — requires audit trail behaviour specification before implementation. Batch mutations must be traceable per-lead.

---

## Later — Identified, Not Yet Scheduled

### DIFC Ingestion (Dubai International Financial Centre)
**Problem:** Dubai-registered entities are a significant part of Arie's target client profile. They are not currently captured.
**Why it matters:** Missing an entire jurisdiction means a material gap in lead coverage for cross-border financial clients.
**Expected outcome:** DIFC-incorporated holding companies, investment vehicles, and fund structures appear in the lead queue alongside UK and Mauritius entities.
**Status:** Later — blocked until Mauritius achieves 10 consecutive clean pipeline runs. See `docs/decisions/0001-difc-gibraltar-parked.md`.

---

### Gibraltar Ingestion
**Problem:** Gibraltar-registered entities are not currently captured.
**Why it matters:** Gibraltar has a meaningful population of regulated financial entities that match Arie's target profile.
**Expected outcome:** Gibraltar entities appear in the lead queue.
**Status:** Later — same gate as DIFC. Activate after DIFC is stable.

---

### Introducer Automation
**Problem:** Introducers are currently entered via manual CSV upload. There is no automated feed or update mechanism.
**Why it matters:** Manual uploading creates a lag between an introducer relationship being established and it appearing in the platform. It also depends on someone remembering to upload.
**Expected outcome:** New introducers are automatically detected and added to the platform. Existing records are updated without manual intervention.
**Status:** Later — requires introducer data source identification and API or integration design.

---

### Pilot Command Center
**Problem:** There is no single view showing whether the platform is actually improving RM outcomes over time.
**Why it matters:** Without trend data, it is impossible to know if lead quality is improving, if contactability is real, or if the scoring model needs adjustment.
**Expected outcome:** Management has a truth layer showing lead quality trend, contactability rate, score stability, and RM action outcomes over rolling time windows.
**Status:** Later — requires sufficient operational data to make trends meaningful (minimum 4–6 weeks of live usage).

---

### Automated Lead Routing
**Problem:** RMs manually assign leads to themselves or each other. There are no rules-based routing criteria.
**Why it matters:** At scale, manual assignment creates delays and inconsistency. Leads may sit unassigned for days.
**Expected outcome:** Leads are automatically routed to the correct RM based on jurisdiction, entity type, or introducer relationship — with manual override always available.
**Status:** Later — routing rules need to be agreed and documented before implementation.

---

## Parked — Waiting on External Gate

| Item | Waiting on |
|---|---|
| DIFC ingestion | Mauritius: 10 consecutive clean runs |
| Gibraltar ingestion | Same gate as DIFC |
| Contact enrichment integration | GDPR compliance checklist + provider DPA signed |

---

## Removed / Not Building

| Item | Reason |
|---|---|
| AI/ML scoring inference | Deterministic scoring is preferred. Non-deterministic models reduce explainability and auditability — both are required in a regulated context. |
| Automated outreach (auto-send emails) | Human review of every outreach is required. Auto-sending without RM approval is not appropriate for financial services relationship management. |
| Web presence / competitive intelligence (SimilarWeb etc.) | High complexity, low ROI vs. the current lead profile. Defer until core enrichment is working. |
| Roadmap UI tab inside the application | RMs do not need to see product roadmap information. This document is the correct home. |
