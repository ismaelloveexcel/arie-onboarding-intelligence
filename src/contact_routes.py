"""Validation for manually-researched / imported contact routes.

Pure, DB-free logic shared by the import script and its tests. It enforces the
compliance rules for contact-route data: real source, real evidence, valid URLs,
and — critically — that guessed or personal emails are never treated as a
verified company route or counted as ready_to_contact.
"""

from __future__ import annotations

import re

from src.route_intelligence import extract_domain

CONFIDENCE_VALUES = {"high", "medium", "low"}
ENTRY_METHODS = {"manual", "import"}

# Local-parts that denote a company/role mailbox rather than a person.
_GENERIC_LOCALPARTS = {
    "info",
    "contact",
    "contacts",
    "hello",
    "enquiries",
    "inquiries",
    "enquiry",
    "admin",
    "office",
    "mail",
    "sales",
    "support",
    "team",
    "general",
    "compliance",
    "kyc",
}
_PUBLIC_EMAIL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "hotmail.com",
    "outlook.com",
    "live.com",
    "icloud.com",
    "me.com",
    "yahoo.com",
    "ymail.com",
    "proton.me",
    "protonmail.com",
    "aol.com",
    "gmx.com",
}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URL_RE = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)


def is_valid_url(value: str | None) -> bool:
    return bool(value) and bool(_URL_RE.match(value.strip()))


def classify_email(email: str | None) -> str:
    """Return 'generic', 'personal', 'public_provider', or 'invalid'."""
    text = (email or "").strip().lower()
    if not text or not _EMAIL_RE.match(text):
        return "invalid"
    localpart, _, domain = text.partition("@")
    if domain in _PUBLIC_EMAIL_DOMAINS:
        return "public_provider"
    if localpart in _GENERIC_LOCALPARTS:
        return "generic"
    # A dotted/underscored human-name pattern (john.smith) reads as personal.
    if re.search(r"[._]", localpart) and not localpart.isdigit():
        return "personal"
    return "personal"


def _email_matches_site(email: str, *urls: str | None) -> bool:
    email_domain = extract_domain(email)
    if not email_domain:
        return False
    for url in urls:
        if url and extract_domain(url) == email_domain:
            return True
    return False


def validate_route_row(
    row: dict[str, str], *, update_existing: bool = False
) -> dict[str, object]:
    """Validate one import row.

    Returns {ok, errors, warnings, normalized, compliant_paths}. A row is only
    accepted when it identifies a company, carries a real source URL and
    evidence, and provides at least one compliant contact path. Personal/guessed
    emails never form a compliant path.
    """
    g = lambda key: (row.get(key) or "").strip()  # noqa: E731

    errors: list[str] = []
    warnings: list[str] = []

    company_id = g("company_id")
    company_number = g("company_number")
    if not company_id and not company_number:
        errors.append("Missing company_id and company_number")

    source_url = g("source_url")
    if not source_url:
        errors.append("Missing source_url")
    elif not is_valid_url(source_url):
        errors.append("Malformed source_url")

    if not g("evidence_summary"):
        errors.append("Missing evidence_summary")

    confidence = g("confidence").lower()
    if confidence and confidence not in CONFIDENCE_VALUES:
        errors.append(f"Unsupported confidence '{confidence}'")
    elif not confidence:
        errors.append("Missing confidence")

    entry_method = g("route_entry_method").lower() or "import"
    if entry_method not in ENTRY_METHODS:
        errors.append(f"Unsupported route_entry_method '{entry_method}'")

    # URL fields must be well-formed when present.
    url_fields = {
        "website_url": g("website_url"),
        "contact_page_url": g("contact_page_url"),
        "contact_form_url": g("contact_form_url"),
        "linkedin_company_url": g("linkedin_company_url"),
    }
    for field, value in url_fields.items():
        if value and not is_valid_url(value):
            errors.append(f"Malformed {field}")

    # Email handling — personal/guessed emails never count as a company route.
    email = g("generic_business_email")
    email_class = classify_email(email) if email else None
    accepted_email = ""
    if email:
        if email_class == "invalid":
            errors.append("Malformed generic_business_email")
        elif email_class in {"personal", "public_provider"}:
            errors.append(
                "Email looks personal/non-official; cannot be a verified company route"
            )
        else:  # generic
            accepted_email = email.strip()
            if not _email_matches_site(
                email, url_fields["website_url"], url_fields["contact_page_url"]
            ):
                warnings.append(
                    "Generic email domain does not match the company website domain — "
                    "confirm it is from an official/registry source"
                )

    introducer = g("introducer_or_csp_name")

    # A compliant contact path = company website, contact form, a *generic*
    # company email, or a named introducer/CSP route.
    compliant_paths: list[str] = []
    if is_valid_url(url_fields["website_url"]):
        compliant_paths.append("website")
    if is_valid_url(url_fields["contact_form_url"]):
        compliant_paths.append("contact_form")
    if accepted_email:
        compliant_paths.append("generic_email")
    if introducer:
        compliant_paths.append("introducer")
    if not compliant_paths:
        errors.append("No compliant contact path")

    normalized = {
        "company_id": company_id or None,
        "company_number": company_number or None,
        "company_name": g("company_name") or None,
        "jurisdiction": g("jurisdiction") or None,
        "website": url_fields["website_url"] or None,
        "contact_page_url": url_fields["contact_page_url"] or None,
        "contact_form_url": url_fields["contact_form_url"] or None,
        "generic_email": accepted_email or None,
        "linkedin_url": url_fields["linkedin_company_url"] or None,
        "introducer_or_csp_name": introducer or None,
        "introducer_or_csp_route": g("introducer_or_csp_route") or None,
        "source_url": source_url or None,
        "source_label": g("source_label") or None,
        "source_type": g("source_type") or None,
        "confidence": confidence or None,
        "evidence_summary": g("evidence_summary") or None,
        "route_entry_method": entry_method,
        "notes": g("notes") or None,
        "update_existing": update_existing,
    }

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "compliant_paths": compliant_paths,
        "normalized": normalized,
    }


def dedupe_key(normalized: dict[str, object]) -> str:
    """Identity for duplicate detection within a batch."""
    return (
        str(normalized.get("company_id") or normalized.get("company_number") or "").lower()
    )
