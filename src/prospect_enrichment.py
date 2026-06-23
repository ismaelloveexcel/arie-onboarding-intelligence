"""Phase 1 — RM/commercial enrichment schema, validation, and Ready-to-Work gate.

Pure, DB-free logic shared by the enrichment importer, export template, and
tests. This is a separate RM/commercial readiness layer — it does NOT touch the
technical scoring model. It only decides whether an externally-researched row is
evidence-backed enough to become an RM next action.
"""

from __future__ import annotations

from src.contact_routes import classify_email, is_valid_url

# --- Allowed values ---------------------------------------------------------
PROSPECT_QUALITY_GRADES = {"A", "B", "C", "D"}
RESEARCH_STATUSES = {"not_researched", "researched", "needs_review", "ready", "rejected"}
ROUTE_QUALITIES = {"high", "medium", "low", "unusable"}
SOURCE_RELIABILITIES = {"official", "regulator", "registry", "reputable_third_party", "weak"}
NEXT_RM_ACTIONS = {"contact_now", "research_route", "route_via_introducer", "hold", "reject"}
RM_STATUSES = {"not_started", "contacted", "replied", "meeting", "won", "lost", "not_suitable"}
SHORTLIST_FLAGS = {"true", "false"}
ROUTE_ENTRY_METHODS = {"manual", "import", "system_detected"}

# --- Field lists ------------------------------------------------------------
# The 25 RM/commercial enrichment fields (Phase 1, point 1).
ENRICHMENT_FIELDS = [
    "prospect_quality_grade",
    "rm_priority_rank",
    "prospect_segment",
    "likely_arie_service_need",
    "likely_payment_use_case",
    "business_model_summary",
    "target_buyer_type",
    "suggested_opening_angle",
    "best_contact_route",
    "route_quality",
    "source_reliability",
    "research_status",
    "last_researched_date",
    "next_rm_action",
    "disqualification_reason",
    "management_shortlist_flag",
    "route_entry_method",
    "checked_by",
    "checked_at",
    "rm_owner",
    "rm_status",
    "last_rm_action_date",
    "next_follow_up_date",
    "rm_outcome_notes",
    "lost_reason",
]

# Identity + provenance + route-discovery fields carried on an import row.
IMPORT_IDENTITY_FIELDS = ["company_id", "company_number", "company_name", "jurisdiction"]
ROUTE_DISCOVERY_FIELDS = [
    "website_url",
    "contact_page_url",
    "generic_business_email",
    "contact_form_url",
    "linkedin_company_url",
    "introducer_or_csp_name",
    "introducer_or_csp_route",
]
PROVENANCE_FIELDS = ["source_url", "source_label", "source_type", "evidence_summary"]

# Full import schema (what Manus + Perplexity return).
IMPORT_FIELDS = (
    IMPORT_IDENTITY_FIELDS + ROUTE_DISCOVERY_FIELDS + PROVENANCE_FIELDS + ENRICHMENT_FIELDS
)

# Readiness buckets (RM-facing outcome of an enrichment row).
READY_TO_WORK = "ready_to_work"
RESEARCH_ROUTE = "research_route"
HOLD = "hold"
REJECT = "reject"

_UNKNOWNS = {"", "unknown", "n/a", "na", "none"}


def _g(row, key):
    return (row.get(key) or "").strip()


def _has(value):
    return bool(value) and value.strip().lower() not in _UNKNOWNS


def _route_is_personal_email(best_contact_route):
    """A best_contact_route that is an email must be a generic company mailbox."""
    text = (best_contact_route or "").strip()
    if "@" in text and " " not in text:
        return classify_email(text) in {"personal", "public_provider"}
    return False


def ready_to_work(row) -> bool:
    """The Ready-to-Work formula (Phase 1, point 5). All conditions required."""
    grade = _g(row, "prospect_quality_grade").upper()
    route_quality = _g(row, "route_quality").lower()
    reliability = _g(row, "source_reliability").lower()
    next_action = _g(row, "next_rm_action").lower()
    best_route = _g(row, "best_contact_route")
    email = _g(row, "generic_business_email")

    if grade != "A":
        return False
    if not _has(_g(row, "likely_arie_service_need")):
        return False
    if not _g(row, "business_model_summary"):
        return False
    if not _g(row, "suggested_opening_angle"):
        return False
    if not best_route or best_route.lower() == "registry_only":
        return False
    if route_quality not in {"high", "medium"}:
        return False
    if not is_valid_url(_g(row, "source_url")):
        return False
    if not _g(row, "evidence_summary"):
        return False
    if reliability in {"", "weak"}:
        return False
    if next_action not in {"contact_now", "route_via_introducer"}:
        return False
    # Hard safety guards — guessed/personal emails never become Ready to Work.
    if email and classify_email(email) in {"personal", "public_provider", "invalid"}:
        return False
    if _route_is_personal_email(best_route):
        return False
    return True


def rm_readiness_bucket(row) -> str:
    """Map an enrichment row to exactly one RM outcome bucket."""
    if ready_to_work(row):
        return READY_TO_WORK
    grade = _g(row, "prospect_quality_grade").upper()
    next_action = _g(row, "next_rm_action").lower()
    research_status = _g(row, "research_status").lower()
    if grade == "D" or next_action == "reject" or research_status == "rejected":
        return REJECT
    if next_action == "hold":
        return HOLD
    return RESEARCH_ROUTE


def _is_int(value: str) -> bool:
    try:
        int(str(value).strip())
        return True
    except (TypeError, ValueError):
        return False


def _check_allowed(row, key, allowed, errors, *, lower=True):
    raw = _g(row, key)
    if not raw:
        return
    value = raw.lower() if lower else raw
    if value not in allowed:
        errors.append(f"Unsupported {key} '{raw}'")


def validate_enrichment_row(row: dict[str, str]) -> dict[str, object]:
    """Validate one enrichment import row against the Phase 1 contract.

    Returns {ok, errors, warnings, bucket, ready_to_work, normalized}.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not _g(row, "company_id") and not _g(row, "company_number"):
        errors.append("Missing company_id and company_number")

    grade = _g(row, "prospect_quality_grade").upper()
    if not grade:
        errors.append("Missing prospect_quality_grade")
    elif grade not in PROSPECT_QUALITY_GRADES:
        errors.append(f"Invalid prospect_quality_grade '{grade}' (must be A/B/C/D)")

    # Enum allowed-value enforcement.
    _check_allowed(row, "research_status", RESEARCH_STATUSES, errors)
    _check_allowed(row, "route_quality", ROUTE_QUALITIES, errors)
    _check_allowed(row, "source_reliability", SOURCE_RELIABILITIES, errors)
    _check_allowed(row, "next_rm_action", NEXT_RM_ACTIONS, errors)
    _check_allowed(row, "rm_status", RM_STATUSES, errors)
    _check_allowed(row, "management_shortlist_flag", SHORTLIST_FLAGS, errors)
    _check_allowed(row, "route_entry_method", ROUTE_ENTRY_METHODS, errors)

    rank = _g(row, "rm_priority_rank")
    if rank and not _is_int(rank):
        errors.append(f"rm_priority_rank must be an integer (got '{rank}')")

    source_url = _g(row, "source_url")
    if source_url and not is_valid_url(source_url):
        errors.append("Malformed source_url")
    for url_field in ("website_url", "contact_page_url", "contact_form_url", "linkedin_company_url"):
        v = _g(row, url_field)
        if v and not is_valid_url(v):
            errors.append(f"Malformed {url_field}")

    ab = grade in {"A", "B"}
    if ab and not is_valid_url(source_url):
        errors.append("A/B rows require a valid source_url")
    if ab and not _g(row, "evidence_summary"):
        errors.append("A/B rows require evidence_summary")
    if ab and not _g(row, "best_contact_route"):
        errors.append("A/B rows require best_contact_route")

    if grade == "A":
        if _g(row, "route_quality").lower() not in {"high", "medium"}:
            errors.append("A rows require route_quality high or medium")
        reliability = _g(row, "source_reliability").lower()
        if not reliability:
            errors.append("A rows require source_reliability")
        elif reliability == "weak":
            errors.append("A rows require source_reliability not weak")
        if not _has(_g(row, "likely_arie_service_need")):
            errors.append("A rows require likely_arie_service_need (not unknown)")
        if not _g(row, "business_model_summary"):
            errors.append("A rows require business_model_summary")
        if not _g(row, "suggested_opening_angle"):
            errors.append("A rows require suggested_opening_angle")

    if grade == "D" and not _g(row, "disqualification_reason"):
        errors.append("D rows require disqualification_reason")

    if _g(row, "research_status").lower() in {"researched", "ready", "rejected"} and not _g(row, "last_researched_date"):
        errors.append("researched/ready/rejected rows require last_researched_date")

    if grade in {"A", "B", "C"} and not _g(row, "next_rm_action"):
        errors.append("A/B/C rows require next_rm_action")

    if _g(row, "rm_status").lower() in {"lost", "not_suitable"} and not _g(row, "lost_reason"):
        errors.append("lost/not_suitable rm_status requires lost_reason")

    # Personal/guessed email safety.
    email = _g(row, "generic_business_email")
    if email and classify_email(email) in {"personal", "public_provider"}:
        errors.append("Guessed/personal email is not a verified company route")
    elif email and classify_email(email) == "invalid":
        errors.append("Malformed generic_business_email")
    if _route_is_personal_email(_g(row, "best_contact_route")):
        errors.append("best_contact_route is a personal email; not allowed")

    bucket = rm_readiness_bucket(row)
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "bucket": bucket,
        "ready_to_work": bucket == READY_TO_WORK,
        "normalized": {k: (_g(row, k) or None) for k in IMPORT_FIELDS},
    }
