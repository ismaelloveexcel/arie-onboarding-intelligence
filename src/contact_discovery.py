"""Deterministic, review-gated contact route discovery."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse


SUGGESTION_TYPES = {
    "website",
    "contact_page",
    "generic_email",
    "company_linkedin",
    "registry",
    "regulator",
    "csp_route",
    "introducer_route",
}
SUGGESTION_STATUSES = {"Needs Review", "Accepted", "Rejected"}
CONFIDENCE_VALUES = {"Low": 0.33, "Medium": 0.66, "High": 1.0}
_SEARCH_HOSTS = {"google.com", "www.google.com"}
_PERSONAL_EMAIL_DOMAINS = {
    "gmail.com",
    "hotmail.com",
    "icloud.com",
    "outlook.com",
    "proton.me",
    "protonmail.com",
    "yahoo.com",
}


def _search_url(query: str) -> str:
    return f"https://www.google.com/search?{urlencode({'q': query})}"


def _suggestion(
    *,
    company_id: str,
    suggestion_type: str,
    suggested_value: str,
    source_name: str,
    source_url: str,
    search_query: str,
    confidence: str,
    confidence_reason: str,
) -> dict[str, str]:
    fingerprint_input = "|".join(
        [company_id, suggestion_type, suggested_value, source_url, search_query]
    )
    return {
        "company_id": company_id,
        "suggestion_type": suggestion_type,
        "suggested_value": suggested_value,
        "source_name": source_name,
        "source_url": source_url,
        "search_query": search_query,
        "confidence": confidence,
        "confidence_reason": confidence_reason,
        "status": "Needs Review",
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "fingerprint": hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest(),
    }


def build_contact_discovery_suggestions(
    *,
    company_id: str,
    company_name: str,
    jurisdiction: str,
    source_ref: str | None = None,
    registered_address: str | None = None,
    verify_url: str | None = None,
    officer_names: list[str] | None = None,
) -> list[dict[str, str]]:
    """Build public-source review routes without fetching result pages."""
    identity = f'"{company_name}" {source_ref or ""}'.strip()
    suggestions: list[dict[str, str]] = []

    if verify_url:
        source_name = (
            "Companies House"
            if jurisdiction == "UK"
            else "Mauritius CBRD"
            if jurisdiction == "Mauritius"
            else "Official registry"
        )
        suggestions.append(
            _suggestion(
                company_id=company_id,
                suggestion_type="registry",
                suggested_value=verify_url,
                source_name=source_name,
                source_url=verify_url,
                search_query=identity,
                confidence="High",
                confidence_reason="Official registry route attached to the legal entity record.",
            )
        )

    searches = [
        (
            "website",
            f'"{company_name}" {jurisdiction} official website',
            "Official website search",
        ),
        ("contact_page", f'"{company_name}" contact', "Company contact page search"),
        (
            "company_linkedin",
            f'"{company_name}" site:linkedin.com/company OR site:linkedin.com/showcase',
            "Company LinkedIn search",
        ),
    ]
    for suggestion_type, query, source_name in searches:
        search_url = _search_url(query)
        suggestions.append(
            _suggestion(
                company_id=company_id,
                suggestion_type=suggestion_type,
                suggested_value=search_url,
                source_name=source_name,
                source_url=search_url,
                search_query=query,
                confidence="Low",
                confidence_reason="Search route only; the result must be opened and verified by an RM.",
            )
        )

    if jurisdiction == "Mauritius":
        regulator_query = f"{identity} site:fscmauritius.org"
        regulator_name = "Mauritius FSC search"
    elif jurisdiction == "UK":
        regulator_query = f"{identity} site:register.fca.org.uk"
        regulator_name = "FCA register search"
    else:
        regulator_query = f"{identity} regulator register"
        regulator_name = "Regulator search"
    regulator_url = _search_url(regulator_query)
    suggestions.append(
        _suggestion(
            company_id=company_id,
            suggestion_type="regulator",
            suggested_value=regulator_url,
            source_name=regulator_name,
            source_url=regulator_url,
            search_query=regulator_query,
            confidence="Low",
            confidence_reason="Restricted regulator search; entity relationship requires review.",
        )
    )

    csp_query = (
        f'"{registered_address}" "{company_name}" management company OR fiduciary '
        "OR corporate services"
        if registered_address
        else f'"{company_name}" management company OR fiduciary OR corporate services'
    )
    csp_url = _search_url(csp_query)
    suggestions.append(
        _suggestion(
            company_id=company_id,
            suggestion_type="csp_route",
            suggested_value=csp_url,
            source_name="Registered-office / CSP search",
            source_url=csp_url,
            search_query=csp_query,
            confidence="Low",
            confidence_reason=(
                "Registered-office search may identify a CSP, but no relationship is assumed."
                if registered_address
                else "CSP search route only; no relationship is assumed."
            ),
        )
    )

    active_names = [name.strip() for name in officer_names or [] if name.strip()][:3]
    if active_names:
        officer_terms = " OR ".join(f'"{name}"' for name in active_names)
        introducer_query = (
            f'"{company_name}" ({officer_terms}) management company OR introducer'
        )
    else:
        introducer_query = f'"{company_name}" introducer OR management company'
    introducer_url = _search_url(introducer_query)
    suggestions.append(
        _suggestion(
            company_id=company_id,
            suggestion_type="introducer_route",
            suggested_value=introducer_url,
            source_name="Introducer route search",
            source_url=introducer_url,
            search_query=introducer_query,
            confidence="Low",
            confidence_reason="Public search route only; introducer relationship requires evidence.",
        )
    )
    return suggestions


def acceptance_target(suggestion_type: str, suggested_value: str) -> str | None:
    """Return the Contact Research field only for a concrete safe value."""
    value = suggested_value.strip()
    if suggestion_type == "generic_email":
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value):
            return None
        domain = value.rsplit("@", 1)[1].lower()
        return None if domain in _PERSONAL_EMAIL_DOMAINS else "generic_email"
    if suggestion_type not in {"website", "contact_page", "company_linkedin"}:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    host = parsed.netloc.lower().split(":", 1)[0]
    if host in _SEARCH_HOSTS:
        return None
    if suggestion_type == "company_linkedin":
        linkedin_host = host == "linkedin.com" or host.endswith(".linkedin.com")
        company_path = parsed.path.startswith(("/company/", "/showcase/"))
        return "linkedin_url" if linkedin_host and company_path else None
    return "website" if suggestion_type == "website" else "contact_form_url"


def prepare_contact_acceptance(
    *,
    existing: dict[str, str | None],
    suggestion: dict[str, str],
    reviewer: str,
    reviewed_at: datetime | None = None,
) -> tuple[dict[str, str | None], str | None]:
    """Merge an accepted suggestion into Contact Research without outreach."""
    if suggestion.get("status") != "Needs Review":
        raise ValueError("Suggestion has already been reviewed")
    suggestion_type = suggestion.get("suggestion_type", "")
    if suggestion_type not in SUGGESTION_TYPES:
        raise ValueError("Unsupported suggestion type")
    confidence = suggestion.get("confidence", "")
    if confidence not in CONFIDENCE_VALUES:
        raise ValueError("Unsupported confidence")

    target = acceptance_target(suggestion_type, suggestion.get("suggested_value", ""))
    merged = dict(existing)
    if target:
        merged[target] = suggestion["suggested_value"].strip()
        merged["source"] = (
            f"{suggestion.get('source_name') or 'Public source'}: "
            f"{suggestion.get('source_url') or suggestion['suggested_value']}"
        )
        merged["confidence"] = str(CONFIDENCE_VALUES[confidence])
        merged["verified_at"] = (reviewed_at or datetime.now(timezone.utc)).date().isoformat()
        merged["checked_by"] = reviewer
    return merged, target


def review_status(current_status: str, decision: str) -> str:
    if current_status != "Needs Review":
        raise ValueError("Suggestion has already been reviewed")
    if decision not in {"Accepted", "Rejected"}:
        raise ValueError("Invalid review decision")
    return decision
