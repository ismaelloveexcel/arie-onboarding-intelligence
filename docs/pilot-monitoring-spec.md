# Pilot Monitoring Spec — First 7 Days

Last updated: 2026-06-05

> This document defines what to observe, measure, and record during the first 7 days of live RM usage.
> The goal is to generate signal, not noise. Every observation should answer one of:
> * Is the decision system working as intended?
> * Where is RM friction highest?
> * What assumptions were wrong?

---

## Observation framework

Run a brief check-in with each RM after days 1, 3, and 7. Keep it short — 3 questions maximum per session. Record answers here.

---

## Metrics to track (from system data)

These can be extracted from the database at any point.

### 1. Session depth
**What:** How many lead detail pages does each RM open per session?
**Why:** If average is fewer than 3, queue friction (especially scroll loss) is the cause.
**SQL proxy:**
```sql
SELECT actor, COUNT(DISTINCT entity_id) AS leads_reviewed, DATE(created_at)
FROM audit_log
WHERE entity_type = 'company' AND action = 'rm_action_updated'
GROUP BY actor, DATE(created_at)
ORDER BY DATE(created_at) DESC;
```

### 2. Time-to-first-contact
**What:** For leads that get a `contacted_at` date, how many days elapsed from the lead appearing in the queue (incorporation date as proxy) to first contact?
**Why:** This is the core metric the platform is designed to reduce.
**SQL proxy:**
```sql
SELECT
  c.company_name,
  c.incorporation_date,
  ra.contacted_at,
  (ra.contacted_at - c.incorporation_date) AS days_to_contact,
  ra.assigned_to
FROM rm_actions ra
JOIN companies c ON c.id = ra.company_id
WHERE ra.contacted_at IS NOT NULL
ORDER BY ra.contacted_at DESC;
```

### 3. Status distribution
**What:** How are leads being triaged — how many are Contacted, Reviewing, Not Fit, vs New?
**Why:** If almost nothing moves out of "New", RMs are not using the workflow states. If "Not Fit" is very high, the scoring model may be surfacing irrelevant leads.
**SQL proxy:**
```sql
SELECT status, COUNT(*) FROM rm_actions GROUP BY status ORDER BY COUNT(*) DESC;
```

### 4. Follow-up compliance
**What:** What percentage of contacted leads have a follow-up date set?
**Why:** Low follow-up rate means either RMs are not using follow-ups, or the auto-population isn't working as expected.
**SQL proxy:**
```sql
SELECT
  COUNT(*) FILTER (WHERE contacted_at IS NOT NULL) AS contacted,
  COUNT(*) FILTER (WHERE contacted_at IS NOT NULL AND follow_up_at IS NOT NULL) AS with_followup,
  COUNT(*) FILTER (WHERE follow_up_at < CURRENT_DATE AND status NOT IN ('Client','Closed - Not Fit','Not Fit','Archived')) AS overdue
FROM rm_actions;
```

### 5. Contact intelligence usage
**What:** How many lead contacts are being added per week?
**Why:** Zero additions suggests the Key Contacts section is being ignored. High additions suggests it is useful.
**SQL proxy:**
```sql
SELECT DATE(created_at), COUNT(*) AS contacts_added
FROM lead_contacts
GROUP BY DATE(created_at)
ORDER BY DATE(created_at) DESC;
```

### 6. Excel export usage
**What:** Server log entries for `GET /leads/export`.
**Why:** If nobody uses it, deprioritise future export improvements. If it is used heavily, investing in richer exports is justified.

---

## Qualitative observations to collect (RM check-ins)

### Day 1 debrief (5 minutes)

Ask each RM:

1. When you opened the queue, did you immediately understand what to do?
2. Were there any leads where you were unsure why they appeared?
3. Was there anything you wanted to do that you couldn't find?

Record answers verbatim. Do not explain or defend — only listen.

---

### Day 3 debrief (5 minutes)

Ask each RM:

1. Are you using the follow-up dates? Do they feel useful?
2. When you return to a lead after a few days, is the information you need easy to find?
3. What is the most annoying thing about the platform right now?

---

### Day 7 debrief (10 minutes)

Ask each RM:

1. Of the leads you've reviewed this week, roughly what percentage felt relevant to contact?
2. Has the platform changed how you find or prioritise leads versus before?
3. What is missing that would make your day faster?

---

## Failure mode detection

These signals indicate something is wrong and warrant immediate investigation.

| Signal | What it means | Response |
|---|---|---|
| Average session depth < 3 leads | Scroll position loss or friction is causing RMs to abandon sessions | Prioritise scroll position fix; check if keyboard shortcut awareness needed |
| > 50% of leads left as "New" after 3 days | RMs are not using status workflow | Check if status options are clear; may need RM training |
| No contacts added in first week | Key Contacts section not being used | Ask RMs why; may be habit, may be UX friction |
| "Not Fit" rate > 60% | Scoring is surfacing too many irrelevant leads | Review top scored leads manually; check if scoring signals need recalibration |
| Overdue follow-ups > 20% of contacted leads by day 7 | Follow-up workflow not established | Review auto-follow-up date; check if RMs are aware of overdue indicators in queue |
| Any P0/P1 incident (data corruption, unauthorised access, broken save) | System stability issue | Escalate immediately; do not continue pilot until resolved |

---

## What to do with the data

After 7 days, update `docs/product-roadmap.md` with findings:

- Move any "Next" items that are confirmed by RM feedback to the top of the backlog
- Move any "Next" items that show no evidence of RM pain to "Later"
- Add any new items that RMs identified that were not anticipated

This closes the feedback loop between real usage and planned development.

---

## Definition of a successful pilot week

The pilot week is successful if:

- At least 10 leads have been reviewed (opened) by at least 2 different RMs
- At least 3 leads have a `contacted_at` date entered
- No P0 or P1 incidents were recorded
- At least one RM can describe the platform's purpose to a colleague without prompting

Success does not require the platform to be perfect. It requires it to be usable and to surface accurate signal about what to improve next.
