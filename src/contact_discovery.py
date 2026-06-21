"""Deterministic, review-gated contact route discovery."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse


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
    """Return concrete candidates only.

    The offline operator does not fetch or verify public pages, so it cannot
    honestly produce a contact candidate. Research shortcuts are built at
    render time and must not be persisted as reviewable discoveries.
    """
    return []


def is_search_shortcut(suggested_value: str) -> bool:
    parsed = urlparse(suggested_value.strip())
    host = parsed.netloc.lower().split(":", 1)[0]
    return host in _SEARCH_HOSTS and parsed.path.rstrip("/") == "/search"


def suggestion_category(suggestion_type: str, suggested_value: str) -> str:
    """Classify stored rows without rewriting historical production data."""
    if is_search_shortcut(suggested_value):
        return "shortcut"
    if suggestion_type in {"registry", "regulator"}:
        return "verification"
    if acceptance_target(suggestion_type, suggested_value) is not None:
        return "candidate"
    if suggestion_type in {"csp_route", "introducer_route"} and suggested_value.strip():
        return "candidate"
    return "shortcut"


def build_rm_action_summary(
    *,
    jurisdiction: str,
    entity_type: str | None,
    current_readiness: str,
    company_contact: dict[str, object],
    candidate_routes: list[dict[str, object]],
    owner: str | None,
    route_hint: str | None,
    has_officers: bool,
) -> dict[str, object]:
    """Summarise the most useful deterministic RM contact-route decision."""
    route_fields = [
        ("generic_email", "Company email"),
        ("contact_form_url", "Contact form"),
        ("website", "Verified website"),
        ("linkedin_url", "Company LinkedIn"),
    ]
    saved_route = next(
        (
            (label, str(company_contact[field]))
            for field, label in route_fields
            if company_contact.get(field)
        ),
        None,
    )
    pending_candidates = [
        item for item in candidate_routes if item.get("status") == "Needs Review"
    ]
    best_candidate = pending_candidates[0] if pending_candidates else None
    missing = [
        label
        for field, label in [
            ("website", "Verified website"),
            ("generic_email", "Generic company email"),
            ("contact_form_url", "Contact form"),
            ("linkedin_url", "Verified company LinkedIn"),
        ]
        if not company_contact.get(field)
    ]
    if not any(item.get("type") in {"csp_route", "introducer_route"} for item in candidate_routes):
        missing.append("Confirmed introducer / CSP route")

    if saved_route:
        best_next_action = "Review the saved contact route and prepare the first RM conversation."
        best_available_route = f"{saved_route[0]}: {saved_route[1]}"
        why = "A company-level contact route has been saved with provenance. Confirm it is still current before use."
        confidence = company_contact.get("confidence") or "Not rated"
    elif best_candidate:
        best_next_action = f"Review the {str(best_candidate.get('type_label', 'contact route')).lower()} candidate."
        best_available_route = str(best_candidate.get("value") or "Candidate route")
        why = "A concrete candidate is available, but it has not yet been verified or accepted into Contact Research."
        confidence = best_candidate.get("confidence") or "Not rated"
    elif route_hint or jurisdiction == "Mauritius":
        best_next_action = "Research management company / CSP route first."
        best_available_route = "No candidate contact route found yet"
        why = (
            "No verified company website or generic contact route is available. "
            "For this Mauritius entity, the practical route may be through an "
            "administrator, management company, CSP, fiduciary, or registered office provider."
        )
        confidence = "Medium - rule-based recommendation"
    elif has_officers:
        best_next_action = "Research the active director / officer route first."
        best_available_route = "No candidate contact route found yet"
        why = "No company-level route is verified, but an active officer provides the strongest available research path."
        confidence = "Medium - rule-based recommendation"
    else:
        best_next_action = "Research the official website and contact page first."
        best_available_route = "No candidate contact route found yet"
        why = "No verified company-level or person-level contact route is currently available."
        confidence = "Low - research required"

    return {
        "current_readiness": current_readiness,
        "best_next_action": best_next_action,
        "best_available_route": best_available_route,
        "why": why,
        "missing": missing,
        "confidence": confidence,
        "owner": owner or "Unassigned",
        "last_checked": company_contact.get("last_checked"),
    }


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
