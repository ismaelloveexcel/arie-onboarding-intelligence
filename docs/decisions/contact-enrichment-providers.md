# Contact Enrichment Provider Decision

## Purpose

This document governs the decision to integrate a third-party contact enrichment API into the platform.
No integration should begin until this document records an explicit APPROVED decision with all criteria evaluated.

**Status: PENDING DECISION**

---

## Why this decision matters

Contact enrichment is high-impact (directly enables RM outreach) but carries real risks:
- GDPR/data protection exposure if personal data is stored or processed incorrectly
- Data quality variance between providers (email bounce rates, stale records)
- Cost per-lookup can be significant at volume
- API dependency introduces a new operational failure surface

A poor choice here creates technical debt, compliance risk, and RM trust damage if contact data is wrong.

---

## Candidate Providers

### 1. Hunter.io

| Criteria | Assessment |
|---|---|
| **Coverage** | Strong for UK professional email discovery (domain-based). Works well for Ltd/PLC companies with a web presence. |
| **Data quality** | Email confidence scores provided. Bounce rate ~5–8% reported. |
| **GDPR** | Business emails only. Hunter claims GDPR compliance. Requires DPA review before use. |
| **Cost** | ~$49/month for 500 requests. ~$149/month for 2,500. Pay-per-lookup also available. |
| **Latency** | Synchronous API, response in <1s. |
| **Gaps** | No phone numbers. Weak on Mauritius-registered entities. |

---

### 2. Clearbit (now Apollo.io / HubSpot)

| Criteria | Assessment |
|---|---|
| **Coverage** | Broad: email, phone, LinkedIn, company firmographics. Strong US/UK coverage. |
| **Data quality** | Variable. Company data is good; contact-level data can be outdated. |
| **GDPR** | Requires explicit DPA. Known to have received regulatory scrutiny in EU. |
| **Cost** | Pricing opaque; enterprise-tier. Likely $500–$2,000+/month at scale. |
| **Latency** | Enrichment API is synchronous, <2s. |
| **Gaps** | Expensive for volume. Mauritius coverage weak. |

---

### 3. Apollo.io

| Criteria | Assessment |
|---|---|
| **Coverage** | Large database (~275M contacts). Email + phone + LinkedIn. |
| **Data quality** | Email verification included. Mixed reviews on data freshness. |
| **GDPR** | GDPR compliance claimed. Requires DPA. ICO registration check recommended. |
| **Cost** | $49/month (basic, 1,000 credits). Scales to $149+/month. |
| **Latency** | REST API, synchronous, fast. |
| **Gaps** | Less specialist in financial services. Mauritius coverage weak. |

---

### 4. Companies House Officers (already integrated)

| Criteria | Assessment |
|---|---|
| **Coverage** | UK only. Directors and PSC names available. No email or phone. |
| **Data quality** | Authoritative. Government source. |
| **GDPR** | Public register. Legally safe to display. |
| **Cost** | Free (already integrated). |
| **Latency** | Nightly batch enrichment already running. |
| **Gaps** | No contact details (email/phone/LinkedIn). UK only. |

---

### 5. LinkedIn (manual + scraping approaches)

| Criteria | Assessment |
|---|---|
| **Coverage** | Excellent for decision-maker identification. |
| **Data quality** | High — self-reported and current. |
| **GDPR** | Scraping violates LinkedIn ToS. Direct search links (already implemented) are safe. |
| **Cost** | Free via search link. LinkedIn Sales Navigator API requires enterprise agreement. |
| **Latency** | Manual only (search link opens browser). |
| **Gaps** | Cannot automate without ToS violation risk. |

---

## Compliance Requirements (must all be confirmed before go-live)

- [ ] Data Processing Agreement (DPA) signed with chosen provider
- [ ] Privacy notice updated to disclose third-party enrichment
- [ ] Data retention policy defined: how long enriched contact data is stored
- [ ] Right-to-erasure workflow exists for enriched contact records
- [ ] Enrichment source recorded per contact record (already in schema: `enrichment_source`)
- [ ] No personal data stored beyond what is operationally necessary (legal basis: legitimate interest)
- [ ] ICO registration reviewed if processing UK personal data at scale

---

## Recommended decision criteria

Score each provider 1–5 on:
1. Coverage for our lead types (UK Ltd, Mauritius GBC/AC)
2. Email quality / verifiability
3. GDPR compliance confidence
4. Cost at our expected volume (~500–2,000 lookups/month)
5. LinkedIn / decision-maker discovery capability

**Minimum bar for APPROVED:** Score ≥ 16/25, and all compliance requirements committed to.

---

## Recommendation (pending)

Based on current lead profile (primarily UK Ltd / Mauritius GBC with some web presence):

**Initial recommendation: Hunter.io** for email discovery only, with these constraints:
- Business domain emails only (no personal addresses)
- Store confidence score alongside email
- Mark enrichment source as `hunter.io` in `lead_contacts.enrichment_source`
- Revisit at 3-month mark for volume/quality review

**Do not use Clearbit** until pricing and GDPR basis are clearly documented.

**Upgrade path:** Once UK email enrichment proves value, evaluate Apollo.io for phone + LinkedIn coverage.

---

## Decision record

Fill in before any integration work begins:

- **Decision:** APPROVED / REJECTED / DEFERRED
- **Provider chosen:**
- **Date:**
- **Decided by:**
- **DPA signed:** Yes / No
- **Privacy notice updated:** Yes / No
- **Volume limit set:** Yes / No (specify)
- **Review date:**

---

## What is already built (no decision needed)

The following is already in place and requires no enrichment provider:

- LinkedIn search links per contact (director name → LinkedIn search)
- Companies House officer import (UK only)
- Manual contact entry with all fields
- Enrichment-ready schema (`enrichment_source`, `enrichment_status`, `email_confidence`, etc.)
- `company_web_intelligence` table for web presence data

These cover a significant portion of RM needs without any third-party dependency.
