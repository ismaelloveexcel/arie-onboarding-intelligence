# Known Limitations

Last updated: 2026-06-05

> This document records what the platform intentionally does not do yet, and why.
> The distinction between "not built yet" and "broken" matters.
> Six months from now, this document prevents the team from treating intentional deferrals as bugs.

---

## Data Coverage Limitations

### DIFC and Gibraltar leads are not captured
The platform does not ingest company incorporations from Dubai (DIFC) or Gibraltar. These jurisdictions are in scope but parked pending Mauritius pipeline stability. See `docs/decisions/0001-difc-gibraltar-parked.md`.

**Impact:** Leads from these jurisdictions are missing entirely from the queue.
**Workaround:** None currently. Manual research required.
**Activation trigger:** Mauritius achieves 10 consecutive clean pipeline runs.

---

### Mauritius data is entity-level only
The MNS portal provides company name, file number, entity type, and incorporation date. It does not provide director names, beneficial owners, or SIC codes for Mauritius entities.

**Impact:** Mauritius leads have no director data to import as contacts. The "Import from registry" button only appears for UK Companies House leads.
**Workaround:** Add contacts manually on Mauritius lead detail pages.

---

### LEI matching is probabilistic for ambiguous cases
When a company name matches multiple LEI records with similar confidence, the match is flagged as AMBIGUOUS and routed to a review queue rather than auto-linked.

**Impact:** Some companies will not show an LEI card even if they have one registered.
**Workaround:** Check the LEI review queue; manual resolution is possible.

---

## Contact Intelligence Limitations

### No automated contact discovery
Key contacts for leads must be added manually or imported from the Companies House officer registry. There is no automated email, phone, or LinkedIn discovery.

**Impact:** Most leads will open with an empty Key Contacts section, especially in the first weeks of usage.
**Workaround:** Use "Import from registry" to add directors from Companies House, then enrich manually.
**Planned resolution:** Contact enrichment API integration (Hunter.io recommended). Blocked pending GDPR compliance checklist. See `docs/decisions/contact-enrichment-providers.md`.

---

### LinkedIn profiles are search links, not verified records
The LinkedIn column for contacts shows a search link (name → LinkedIn people search), not a verified profile URL, unless the RM manually adds a direct profile URL.

**Impact:** RMs must identify the correct profile manually from search results.
**Workaround:** Enter the verified LinkedIn URL directly in the contact record after confirming identity.

---

## RM Workflow Limitations

### Queue position is lost on back-navigation
When an RM opens a lead detail page and returns to the queue, the browser reloads from the top. Scroll position and pagination position are not preserved.

**Impact:** RMs processing multiple leads sequentially lose their place after each lead visit. Affects productivity in high-volume sessions.
**Workaround:** Open leads in new browser tabs (Ctrl/Cmd+click).
**Planned resolution:** Next development cycle.

---

### No bulk lead processing
Actions (assign, change status, add notes) must be performed one lead at a time. There is no multi-select or batch update capability.

**Impact:** Weekly triage of large lead volumes requires opening each lead individually.
**Workaround:** Use Excel export for offline triage; re-enter priorities individually.
**Planned resolution:** Bulk processing — requires audit trail specification first.

---

### Follow-up visibility is limited to 7 days ahead
The queue shows follow-up indicators only for leads with a follow-up date today, overdue, or within 7 days. Upcoming follow-ups beyond 7 days are not shown.

**Impact:** RMs cannot see the next 2–4 weeks of follow-up activity in the queue.
**Workaround:** Use Excel export filtered by assigned RM and sort by follow-up date.

---

## Introducer Limitations

### Introducers are managed manually only
There is no automated feed for introducer data. All introducers must be entered via CSV upload.

**Impact:** New introducer relationships must be manually uploaded before they appear in the system. There may be a lag between a relationship being established and it being tracked.
**Workaround:** Upload a CSV after each new introducer relationship is established.
**Planned resolution:** Introducer automation — later phase, pending data source identification.

---

### No introducer-to-lead linkage
There is no automated connection between an introducer record and the leads they refer. If an introducer sends a new client, that relationship is not captured in the platform.

**Impact:** Introducer performance analytics (which introducers generate the most clients, which relationships deserve more investment) are not available.
**Workaround:** Track introducer attribution manually in notes or external spreadsheets.
**Planned resolution:** Introducer performance analytics — next phase after RM workflow stabilises.

---

### No introducer performance metrics
The dashboard does not show which introducers are most active, which are converting, or how each introducer relationship compares over time.

**Impact:** Management decisions about introducer relationships rely on memory and external records rather than system data.
**Workaround:** None currently within the platform.
**Planned resolution:** Introducer analytics dashboard — later phase.

---

## Scoring Limitations

### Scoring model does not include web presence signals
There is no website traffic, social media presence, news mention, or digital footprint scoring. Score is based entirely on registry-derived signals.

**Impact:** A company with a strong online presence and obvious banking need may score the same as a dormant shell. Web signals are not differentiated.
**Workaround:** RMs can review the website link on the lead detail page and manually adjust priority via status/notes.
**Planned resolution:** Not currently planned. Complexity vs. ROI does not justify implementation at this stage.

---

### Score does not reflect RM feedback
If an RM marks a lead as "Not Fit", the score does not decrease. The scoring model has no feedback loop from RM outcomes.

**Impact:** Dismissed leads may reappear in future queue runs with the same score.
**Workaround:** Use the "Not Fit" or "Archived" status to remove leads from the active working queue.
**Planned resolution:** Not currently planned. Would require outcome-feedback model design.

---

## Infrastructure Limitations

### Pipeline runs on GitHub Actions, not Railway
The nightly ingestion pipeline runs as a GitHub Actions scheduled workflow, not as a Railway service. If GitHub Actions is unavailable at 02:00 UTC, the pipeline does not run.

**Impact:** No new leads ingested for that day.
**Workaround:** Manually trigger the `daily.yml` workflow from the GitHub Actions UI.

---

### No real-time ingestion
Leads appear in the queue only after the nightly pipeline run. Companies incorporated during the day are not visible until the following morning.

**Impact:** There is a delay of up to 24 hours between a company being incorporated and it appearing as a lead.
**Workaround:** None. This is an architectural constraint of the nightly batch model.
**Planned resolution:** Not currently planned. Real-time ingestion would require significant pipeline redesign.
