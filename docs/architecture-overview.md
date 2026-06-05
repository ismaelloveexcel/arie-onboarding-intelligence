# Architecture Overview — Arie Lead Intelligence Platform

## 1. What problem does this platform solve?

Arie Finance provides banking and financial services to international businesses — holding companies, investment vehicles, fund structures, and corporate entities operating across multiple jurisdictions.

Finding the right clients at the right moment is the hardest part of outbound financial sales. Companies that need banking services rarely announce themselves. They incorporate quietly, register a Legal Entity Identifier (LEI), and begin looking for a banking partner — often within 30–90 days of formation.

The Arie Lead Intelligence Platform solves this by:

- **Automatically identifying** newly incorporated companies across key jurisdictions that match the profile of Arie's target clients
- **Scoring and ranking** them by how likely they are to need banking services in the near term
- **Presenting them to RMs** in a prioritised decision interface with context, contact intelligence, and workflow tools

**Who uses it:** Relationship Managers (RMs) at Arie Finance, plus team leads who need pipeline visibility.

**What they are trying to achieve:** Move from "identify a lead" to "initiate outreach" as fast as possible, with confidence that the lead is relevant and the timing is right.

**Why it exists:** Without this platform, RMs would need to manually monitor company registries, manually assess relevance, and manually research contacts. That process is slow, inconsistent, and unscalable. This platform compresses that work from hours to seconds.

---

## 2. How does a lead move through the system?

```
Company Incorporated (UK, Mauritius, or other jurisdiction)
        ↓
Nightly Data Ingestion
(Companies House API / Mauritius MNS / GLEIF)
        ↓
Scoring Engine
(Rule-based, deterministic, 0–100 score with named signals)
        ↓
Queue Snapshot
(Ranked, filterable lead queue refreshed each night)
        ↓
RM Review
(RM opens lead, sees why it scored, reviews contacts and signals)
        ↓
Outreach Decision
("Contact Now" / "Review Later" / "Do Not Contact")
        ↓
Contact Recorded
(Contacted date entered; follow-up date auto-set +7 days)
        ↓
Follow-Up
(Queue shows overdue/upcoming follow-up indicators)
        ↓
Converted / Closed
(Status updated; audit trail records all actions)
```

Each lead remains in the system permanently. Its score is recalculated nightly if its underlying data changes (e.g. a fresh LEI is registered after the initial incorporation).

---

## 3. Data Sources

### UK — Companies House API

| | |
|---|---|
| **Purpose** | Identify newly incorporated UK companies matching target profile |
| **Data collected** | Company name, incorporation date, entity type, registered address, SIC codes, officers (directors), PSCs (beneficial owners), LEI linkage |
| **Update frequency** | Nightly (GitHub Actions cron, 02:00 UTC) |
| **Failure impact** | No new UK leads ingested until the next successful run. Existing leads and scores unaffected. |
| **Key signals used** | Entity type (holding/investment/fund), SIC codes (financial services), officer nationality, PSC country of residence |

### Mauritius — MNS Portal

| | |
|---|---|
| **Purpose** | Identify newly incorporated Mauritius entities (GBC and AC structures) |
| **Data collected** | Company name, file number, entity type, incorporation date |
| **Update frequency** | Nightly (same pipeline run as Companies House) |
| **Failure impact** | No new Mauritius leads ingested until resolved. MNS is accessed via scraping; portal changes can break ingestion. |
| **Key signals used** | Entity type (GBC = Global Business Company, AC = Authorised Company) — these are Mauritius's highest-value structures for Arie's client profile |

### GLEIF — Legal Entity Identifier Registry

| | |
|---|---|
| **Purpose** | Detect LEI registrations that signal a company is actively pursuing regulated financial activity |
| **Data collected** | LEI code, registration date, entity status, managing LOU, GLEIF record URL |
| **Update frequency** | Nightly LEI backfill job (separate from main pipeline) |
| **Failure impact** | Fresh LEI signal absent from scoring until resolved. Existing LEI records unaffected. |
| **Key signals used** | Fresh LEI (registered ≤90 days): +30 points. Existing LEI: +15 points. A fresh LEI is one of the strongest signals — it means the company is actively opening regulated accounts right now. |

### Parked Sources

| Source | Status | Activation trigger |
|---|---|---|
| DIFC (Dubai) | Parked | Mauritius achieves 10 consecutive clean pipeline runs |
| Gibraltar | Parked | Same gate as DIFC |

---

## 4. Scoring Philosophy

> We prioritise newly incorporated companies showing indicators that they are likely to require banking services in the near term.

The scoring model is rule-based, deterministic, and fully explainable. Every point awarded has a named reason visible to the RM. There are no black-box contributions.

**Core scoring logic:**

| Signal category | What it detects | Max points |
|---|---|---|
| Entity type | Holding companies, investment vehicles, fund structures | 25 |
| Jurisdiction | UK entities; Mauritius GBC/AC structures | 30 |
| LEI activity | Fresh LEI (≤90 days) or existing LEI | 30 |
| SIC codes | Financial holding, fund management, fintech industry codes | 20 |
| Ownership signals | International PSC (cross-border beneficial owner) | 10 |
| Officer signals | Non-UK director (cross-border operational signal) | 10 |
| Name keywords | Financial/international keywords in company name | 10 |
| Recency | Incorporated within 90 days | 5 |

Scores are capped at 100 for display. A company can accumulate signal points above 100 but the displayed score is bounded.

**Tier thresholds:**
- HIGH: 70–100
- MEDIUM: 40–69
- LOW: 0–39

The scoring version is tracked on every score record. When the model changes, historical scores are preserved and new scores are recalculated with the new version.

---

## 5. RM Workflow

```
Open Lead Queue
→ Review ranked leads (score bar + primary signal chip)
→ Open a lead
→ Read "Why Contact Now" (plain-language explanation)
→ Review Key Contacts (or import director from registry)
→ Make decision: Contact Now / Review Later / Do Not Contact
→ Record outreach (contacted date, notes, assigned RM)
→ Follow-up date auto-populated (+7 days from contacted date)
→ Return to queue — queue shows upcoming/overdue follow-ups inline
→ Export to Excel if batch reporting needed
```

**Introducer workflow (current):**
Introducers (corporate service providers, agents, introducers of business) are tracked separately from leads. They are currently entered via CSV upload and managed manually. There is no automated linkage between an introducer and the leads they refer. This is a known limitation awaiting a future automation phase.

---

## 6. Operational Dependencies

| Dependency | Role | Failure mode |
|---|---|---|
| **Railway** | Hosts the FastAPI web application (UI + API) | App unavailable; data unaffected |
| **GitHub Actions** | Runs nightly ingestion pipeline (02:00 UTC) | No new leads; app still works |
| **PostgreSQL (Railway)** | Stores all company, lead, scoring, RM action, and audit data | Full outage; app unavailable |
| **Companies House API** | Source of UK company data | UK ingestion fails silently; old data preserved |
| **Mauritius MNS portal** | Source of Mauritius incorporation data via scraping | MNS ingestion fails; old data preserved |
| **GLEIF** | Source of LEI records | LEI backfill fails; existing LEI records preserved |
| **Google Fonts** | Inter typeface for UI | UI loads with system fallback font |
| **unpkg.com** | HTMX library for in-page form saves | RM Actions form reverts to full page reload |

**What constitutes a clean pipeline run:**
- `pipeline_runs` table records a run with status `success`
- UK and Mauritius company counts are non-zero
- No exceptions thrown during ingestion or scoring
- Queue snapshot is refreshed (new `refreshed_at` timestamp visible in queue header)
- LEI backfill completes without error

**What to check first if the queue stops updating:**
GitHub Actions logs for the `daily.yml` workflow — not Railway. The Railway service does not run the pipeline; it only hosts the web app.

---

## 7. Governance

The platform is designed to be auditable and safe-to-operate in a regulated financial services context.

**Deterministic scoring:** Every score is reproducible from stored inputs. Scoring version, weights version, and snapshot timestamp are recorded with every score. The same inputs always produce the same output.

**Audit trail:** Every RM action (status change, assignment, notes, contacted date, follow-up date) is logged with actor identity, timestamp, old value, and new value. This trail is immutable and visible on every lead detail page.

**Write authorization:** All data mutation endpoints require a signed actor identity. Unauthorized writes are rejected at the application boundary.

**Mutation isolation:** Only approved service layer modules are permitted to write to core database tables. Static analysis enforces this at CI time.

**CI enforcement:** A single gate engine (`scripts/pilot_gates/gate_engine.py --ci`) validates the entire governance state on every pull request. It cannot be bypassed by injecting flags or editing YAML manually.

---

## 8. Team and routing

| Team member | Role | Lead routing |
|---|---|---|
| Ismael | — | Direct leads |
| Tasneem | — | Direct client leads |
| Aisha | — | Introducer-sourced leads |
| Stephen | — | Introducer-sourced leads |
| Rajesh | — | Introducer-sourced leads |

Routing is currently manual (RM assigns leads to themselves or each other via the RM Actions panel). Automated routing rules are not yet implemented.
