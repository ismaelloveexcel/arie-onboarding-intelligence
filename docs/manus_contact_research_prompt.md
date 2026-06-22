# Manus contact-route research prompt

Paste the prompt below into Manus, attach the exported research batch CSV
(`research_batch.csv` from `scripts/export_research_batch.py`), and ask Manus to
return an enriched CSV. The output feeds `scripts/import_contact_routes.py`.

---

## Prompt to paste into Manus

> You are a compliance-aware B2B research assistant for ARIE Finance, a regulated
> Mauritius payment intermediary. For each company in the attached CSV, find a
> **compliant contact route** using only official, first-party, or public
> sources.
>
> For each row, research and return:
> - `website_url` — the official company website
> - `contact_page_url` — the company's contact page
> - `contact_form_url` — a contact form URL if one exists
> - `generic_business_email` — a company mailbox (info@, contact@, etc.) **taken
>   from the official website or an official register only**
> - `linkedin_company_url` — the company LinkedIn *page* found via public search
> - `introducer_or_csp_name` — the management company / CSP / administrator /
>   registered agent, if the company is reached that way (common for Mauritius
>   global-business entities)
> - `introducer_or_csp_route` — short description of that route
> - `source_url` — the exact URL the evidence came from (**required**)
> - `source_label` — short name of the source (e.g. "Company website", "FSC register")
> - `source_type` — one of: company_website, registry, regulator, csp_directory, other
> - `confidence` — high / medium / low
> - `evidence_summary` — one line explaining what you found and where (**required**)
> - `route_entry_method` — always `manual`
> - `notes` — anything the RM should know
>
> **Rules — do not break these:**
> - Do NOT invent or guess contact details.
> - Do NOT guess email address formats (no "firstname.lastname@" guesses).
> - Do NOT scrape LinkedIn or any closed/login-walled platform; use public search results only.
> - Do NOT return personal/private individual emails. Company mailboxes only.
> - Do NOT mark a personal email as a verified company route.
> - Do NOT return any route without a `source_url`.
> - If you cannot find a compliant route, leave the route fields blank and set
>   `evidence_summary` to "No compliant route found" with your `source_url` of the
>   pages checked. Do not fabricate to fill the row.
>
> Keep the original `company_id`, `company_number`, `company_name`, and
> `jurisdiction` columns unchanged so the import can match the company.

---

## Output columns Manus must return (importer schema)

`company_id, company_number, company_name, jurisdiction, website_url,
contact_page_url, generic_business_email, contact_form_url, linkedin_company_url,
introducer_or_csp_name, introducer_or_csp_route, source_url, source_label,
source_type, confidence, evidence_summary, route_entry_method, notes`
