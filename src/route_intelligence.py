"""Deterministic route-to-conversation intelligence.

This module uses only stored first-party and public-source evidence. It never
fetches pages, guesses personal contact data, changes lead scores, or initiates
outreach.
"""

from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher
from urllib.parse import urlparse


CONTACTABILITY_LABELS = {
    "ready_to_contact": "Ready to Contact",
    "direct_candidate_found": "Direct Candidate Found",
    "route_via_introducer_csp": "Route via Introducer / CSP",
    "management_company_route_likely": "Management Company Route Likely",
    "registry_evidence_only": "Registry Evidence Only",
    "needs_route_research": "Needs Route Research",
    "no_usable_route": "No Usable Route",
}

# --- Simplified contactability status (presentation/API alias layer) ---------
# The seven CONTACTABILITY_LABELS buckets above remain the internal source of
# truth (they carry the finer route grading used for evidence and analytics).
# RM-facing surfaces and any external API consume a simplified four-value
# status. This is a read-time projection only — nothing is persisted from here,
# and no internal bucket is collapsed in storage.
CONTACTABILITY_STATUS_LABELS = {
    "ready_to_contact": "Ready to Contact",
    "route_via_introducer": "Route via Introducer",
    "research_required": "Research Required",
    "no_compliant_route_found": "No Compliant Route Found",
}

# direct_candidate_found maps to research_required on purpose: an online route
# (website/LinkedIn) exists but no contact channel is confirmed, so the honest
# RM action is to research the channel rather than to contact immediately.
_BUCKET_TO_STATUS = {
    "ready_to_contact": "ready_to_contact",
    "direct_candidate_found": "research_required",
    "route_via_introducer_csp": "route_via_introducer",
    "management_company_route_likely": "research_required",
    "registry_evidence_only": "research_required",
    "needs_route_research": "research_required",
    "no_usable_route": "no_compliant_route_found",
}


def contactability_status(bucket: str | None) -> str:
    """Project a 7-value internal bucket onto the 4-value contactability status.

    Unknown or missing buckets fall back to ``research_required`` — the honest
    default when we cannot assert a usable route.
    """
    return _BUCKET_TO_STATUS.get(bucket or "", "research_required")


def contactability_status_label(bucket: str | None) -> str:
    """Human-readable label for the simplified status of an internal bucket."""
    return CONTACTABILITY_STATUS_LABELS[contactability_status(bucket)]


# RM-facing decision verb for the lead-detail action panel. One-to-one with the
# simplified contactability status.
CONTACTABILITY_DECISIONS = {
    "ready_to_contact": "Contact Now",
    "route_via_introducer": "Route via Introducer",
    "research_required": "Research First",
    "no_compliant_route_found": "Do Not Contact Yet",
}


def contactability_decision(bucket: str | None) -> str:
    """The headline RM decision for a recommendation's contactability bucket."""
    return CONTACTABILITY_DECISIONS[contactability_status(bucket)]


SUPPORTED_JURISDICTIONS = {"Mauritius", "UK", "United Kingdom"}

READINESS_RM_READY = "rm_ready"
READINESS_RESEARCH_FIRST = "research_first"
READINESS_NO_ROUTE = "no_compliant_route"

READINESS_LABELS = {
    READINESS_RM_READY: "RM-Ready",
    READINESS_RESEARCH_FIRST: "Research First",
    READINESS_NO_ROUTE: "No Compliant Route",
}


def lead_readiness(
    *,
    recommendation: dict[str, object],
    tier: str | None = None,
) -> dict[str, object]:
    """Deterministic RM-readiness gate over a route recommendation.

    A lead is only RM-ready when it has ALL of: a usable route (direct or
    introducer), confidence of at least medium, attached evidence, a recommended
    next action, and a commercial-fit tier (HIGH/MEDIUM). This is an explainable
    readiness layer on top of scoring and route intelligence — it never changes
    scores or buckets, it only decides whether a lead is honestly worth an RM's
    time right now and, if not, why.

    Returns {'is_rm_ready', 'readiness', 'blocking_reasons'}.
    """
    bucket = recommendation.get("contactability_bucket")
    status = contactability_status(str(bucket) if bucket is not None else None)
    confidence = str(recommendation.get("confidence") or "").lower()
    evidence = recommendation.get("evidence_summary") or []
    next_action = str(recommendation.get("next_action") or "").strip()
    tier_value = str(tier or "").upper()

    reasons: list[str] = []
    if status not in {"ready_to_contact", "route_via_introducer"}:
        reasons.append("No compliant contact or introducer route")
    if confidence not in {"high", "medium"}:
        reasons.append("Route confidence below medium")
    if not evidence:
        reasons.append("No source/evidence attached")
    if not next_action:
        reasons.append("No recommended next action")
    if tier_value and tier_value not in {"HIGH", "MEDIUM"}:
        reasons.append("Commercial fit too weak (low tier)")

    is_rm_ready = not reasons
    if is_rm_ready:
        readiness = READINESS_RM_READY
    elif status == "no_compliant_route_found":
        readiness = READINESS_NO_ROUTE
    else:
        readiness = READINESS_RESEARCH_FIRST

    return {
        "is_rm_ready": is_rm_ready,
        "readiness": readiness,
        "readiness_label": READINESS_LABELS[readiness],
        "blocking_reasons": reasons,
    }


def suggested_opener(
    *,
    company_name: str | None,
    entity_type: str | None,
    jurisdiction: str | None,
    contactability_bucket: str | None,
    best_route_value: str | None = None,
) -> str | None:
    """Return a deterministic draft opener, or None when context is insufficient.

    This is an RM aid, not outreach automation: it never invents contact details,
    only restates stored company facts, and is always flagged for RM review. An
    opener is offered only when there is a usable route to act on (a ready direct
    route or a named introducer/CSP route).
    """
    status = contactability_status(contactability_bucket)
    if status not in {"ready_to_contact", "route_via_introducer"}:
        return None
    name = (company_name or "").strip() or "the company"
    descriptor = " ".join(
        part
        for part in (
            (jurisdiction or "").strip(),
            (entity_type or "").strip().title(),
        )
        if part
    )
    subject = f"{name} ({descriptor})" if descriptor else name
    if status == "route_via_introducer" and (best_route_value or "").strip():
        return (
            f"Draft for RM review — Reach {subject} through the existing relationship "
            f"with {best_route_value.strip()}; introduce ARIE Finance's regulated "
            f"Mauritius payment services and request a warm introduction."
        )
    return (
        f"Draft for RM review — Introduce ARIE Finance's regulated Mauritius payment "
        f"services to {subject} using the verified company route; confirm the saved "
        f"contact route is current before outreach."
    )

_LEGAL_SUFFIXES = {
    "co",
    "company",
    "corp",
    "corporation",
    "inc",
    "incorporated",
    "limited",
    "ltd",
    "llc",
    "plc",
}
_ADDRESS_STOPWORDS = {"mauritius", "republic", "the"}
_PUBLIC_EMAIL_DOMAINS = {
    "gmail.com",
    "hotmail.com",
    "icloud.com",
    "outlook.com",
    "proton.me",
    "protonmail.com",
    "yahoo.com",
}
_MAURITIUS_ROUTE_TERMS = {
    "authorised company",
    "fund",
    "global business",
    "holding",
    "investment",
    "pcc",
}


def normalise_name(value: str | None, *, strip_legal_suffixes: bool = False) -> str:
    tokens = re.findall(r"[a-z0-9]+", (value or "").casefold())
    if strip_legal_suffixes:
        tokens = [token for token in tokens if token not in _LEGAL_SUFFIXES]
    return " ".join(tokens)


def normalise_address(value: str | None) -> str:
    """Normalise an address conservatively for exact/strong comparisons."""
    text = (value or "").casefold()
    replacements = {
        r"\bavenue\b": "ave",
        r"\bbuilding\b": "bldg",
        r"\bfirst\b": "1st",
        r"\bfloor\b": "fl",
        r"\broad\b": "rd",
        r"\bstreet\b": "st",
    }
    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text)
    tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", text)
        if token not in _ADDRESS_STOPWORDS
    ]
    return " ".join(tokens)


def extract_domain(value: str | None) -> str | None:
    text = (value or "").strip().casefold()
    if not text:
        return None
    if "@" in text and "://" not in text:
        domain = text.rsplit("@", 1)[1]
    else:
        parsed = urlparse(text if "://" in text else f"https://{text}")
        domain = parsed.netloc.split(":", 1)[0]
    domain = domain.removeprefix("www.").strip(".")
    return domain or None


def infer_introducer_route_type(introducer: dict[str, object]) -> str:
    category = str(introducer.get("category") or "").casefold()
    name = str(introducer.get("company_name") or "").casefold()
    combined = f"{category} {name}"
    if "fiduciar" in combined or "trust" in combined:
        return "fiduciary"
    if "fund admin" in combined or "fund service" in combined:
        return "fund_administrator"
    if "management" in combined:
        return "management_company"
    if "corporate service" in combined or re.search(r"\bcsp\b", combined):
        return "csp"
    return "introducer"


def build_registered_office_clusters(
    leads: list[dict[str, object]],
) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for lead in leads:
        address = normalise_address(str(lead.get("registered_address") or ""))
        if not address:
            continue
        grouped.setdefault(address, []).append(str(lead["company_id"]))
    return {address: ids for address, ids in grouped.items() if len(ids) > 1}


def _address_similarity(left: str, right: str) -> float:
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _match_fingerprint(lead_id: str, introducer_id: str, match_type: str) -> str:
    return hashlib.sha256(f"{lead_id}|{introducer_id}|{match_type}".encode()).hexdigest()


def match_introducers(
    lead: dict[str, object], introducers: list[dict[str, object]]
) -> list[dict[str, object]]:
    """Return evidence-bearing matches only; weak clues are deliberately omitted."""
    lead_name = normalise_name(str(lead.get("company_name") or ""))
    lead_core = normalise_name(
        str(lead.get("company_name") or ""), strip_legal_suffixes=True
    )
    lead_address = normalise_address(str(lead.get("registered_address") or ""))
    lead_domains = {
        domain
        for domain in (
            extract_domain(str(lead.get("website") or "")),
            extract_domain(str(lead.get("generic_email") or "")),
            extract_domain(str(lead.get("contact_form_url") or "")),
        )
        if domain and domain not in _PUBLIC_EMAIL_DOMAINS
    }
    matches: list[dict[str, object]] = []

    for introducer in introducers:
        introducer_id = str(introducer["id"])
        intro_name = normalise_name(str(introducer.get("company_name") or ""))
        intro_core = normalise_name(
            str(introducer.get("company_name") or ""), strip_legal_suffixes=True
        )
        intro_address = normalise_address(str(introducer.get("address") or ""))
        intro_domains = {
            domain
            for domain in (
                extract_domain(str(introducer.get("contact_email") or "")),
                extract_domain(str(introducer.get("verify_url") or "")),
            )
            if domain and domain not in _PUBLIC_EMAIL_DOMAINS
        }

        match_type = ""
        strength = ""
        evidence = ""
        if lead_name and lead_name == intro_name:
            match_type, strength = "exact_name", "high"
            evidence = "Exact normalised legal-name match to the introducer dataset."
        elif lead_address and lead_address == intro_address:
            match_type, strength = "address", "high"
            evidence = "Exact normalised registered-office match."
        elif shared_domains := sorted(lead_domains & intro_domains):
            match_type, strength = "domain", "high"
            evidence = f"Exact organisation-domain match: {', '.join(shared_domains)}."
        elif (
            lead_core
            and intro_core
            and len(set(lead_core.split()) & set(intro_core.split())) >= 2
            and SequenceMatcher(None, lead_core, intro_core).ratio() >= 0.94
        ):
            match_type, strength = "strong_name", "medium"
            evidence = "Strong normalised-name similarity; relationship requires RM review."
        elif (
            lead_address
            and intro_address
            and len(set(lead_address.split()) & set(intro_address.split())) >= 4
            and _address_similarity(lead_address, intro_address) >= 0.85
        ):
            match_type, strength = "similar_address", "medium"
            evidence = "Strong registered-office similarity; relationship requires RM review."

        if match_type:
            matches.append(
                {
                    "lead_id": str(lead["company_id"]),
                    "introducer_id": introducer_id,
                    "introducer": introducer,
                    "match_type": match_type,
                    "match_strength": strength,
                    "evidence": evidence,
                    "source": str(introducer.get("source") or "Internal introducer list"),
                    "status": "pending",
                    "fingerprint": _match_fingerprint(
                        str(lead["company_id"]), introducer_id, match_type
                    ),
                }
            )

    strength_order = {"high": 0, "medium": 1, "low": 2}
    return sorted(
        matches,
        key=lambda item: (
            strength_order[str(item["match_strength"])],
            str(item["introducer"].get("company_name") or ""),
        ),
    )


def _recommendation_fingerprint(recommendation: dict[str, object]) -> str:
    fields = [
        str(recommendation.get(key) or "")
        for key in (
            "lead_id",
            "contactability_bucket",
            "best_route_type",
            "best_route_value",
            "route_candidate_id",
            "rationale",
            "next_action",
            "confidence",
        )
    ]
    return hashlib.sha256("|".join(fields).encode()).hexdigest()


def build_route_recommendation(
    *,
    lead: dict[str, object],
    introducer_matches: list[dict[str, object]],
    office_cluster_size: int = 0,
    generated_by: str = "route-intelligence-v1",
) -> dict[str, object]:
    """Rank stored evidence into one honest route-to-conversation recommendation."""
    evidence: list[str] = []
    missing: list[str] = []
    generic_email = str(lead.get("generic_email") or "").strip()
    contact_form = str(lead.get("contact_form_url") or "").strip()
    website = str(lead.get("website") or "").strip()
    linkedin = str(lead.get("linkedin_url") or "").strip()
    contact_confidence = str(lead.get("contact_confidence") or "").casefold()
    best_match = introducer_matches[0] if introducer_matches else None

    if generic_email or contact_form:
        route_value = generic_email or contact_form
        bucket = "ready_to_contact"
        route_type = "direct"
        rationale = "A company-level email or contact form is stored with provenance."
        next_action = "RM to verify the saved route is current, then prepare first contact."
        confidence = "high" if contact_confidence == "high" else "medium"
        evidence.append("Stored company contact route with provenance.")
        candidate_id = None
    elif best_match:
        introducer = best_match["introducer"]
        route_value = str(introducer.get("company_name") or "")
        bucket = "route_via_introducer_csp"
        route_type = infer_introducer_route_type(introducer)
        rationale = "Stored evidence links this prospect to an existing route-source record."
        next_action = "RM to review the match evidence and accept or reject the route."
        confidence = str(best_match["match_strength"])
        evidence.append(str(best_match["evidence"]))
        candidate_id = str(introducer["id"])
    elif website or linkedin:
        route_value = website or linkedin
        bucket = "direct_candidate_found"
        route_type = "direct"
        rationale = "A company-level online route exists, but no direct contact channel is confirmed."
        next_action = "RM to verify the website or company LinkedIn route and locate a contact channel."
        confidence = "medium" if contact_confidence in {"high", "medium"} else "low"
        evidence.append("Stored company website or company LinkedIn route.")
        candidate_id = None
    else:
        entity_text = f"{lead.get('entity_type') or ''} {lead.get('company_name') or ''}".casefold()
        is_mauritius_route_entity = lead.get("jurisdiction") == "Mauritius" and any(
            term in entity_text for term in _MAURITIUS_ROUTE_TERMS
        )
        route_value = None
        candidate_id = None
        if is_mauritius_route_entity:
            bucket = "management_company_route_likely"
            route_type = "management_company"
            rationale = (
                "This Mauritius investment/holding structure is commonly reached through "
                "its management company, CSP, fiduciary, administrator, or registered office."
            )
            next_action = "Research and verify the current management company / CSP route."
            confidence = "medium"
            evidence.append("Mauritius entity type supports an intermediary-first route.")
            missing.append("Named management company / CSP candidate")
        elif lead.get("verify_url"):
            bucket = "registry_evidence_only"
            route_type = "registry_only"
            rationale = "The legal entity is verifiable, but no usable contact or relationship route exists."
            next_action = "Use registry evidence to research a direct or intermediary route."
            confidence = "low"
            evidence.append("Official registry route is available.")
            missing.append("Usable contact or relationship route")
        else:
            bucket = "needs_route_research"
            route_type = "research_required"
            rationale = "No direct contact, named intermediary, or registry-backed route is available."
            next_action = "Research a company, person, or intermediary route before contact."
            confidence = "low"
            missing.append("Any evidence-bearing route candidate")

    if office_cluster_size > 1:
        evidence.append(
            f"Registered office is shared by {office_cluster_size} stored prospects."
        )
    elif lead.get("jurisdiction") == "Mauritius" and not lead.get("registered_address"):
        missing.append("Registered office address")
    if not generic_email:
        missing.append("Generic company email")
    if not contact_form:
        missing.append("Contact form")

    recommendation: dict[str, object] = {
        "lead_id": str(lead["company_id"]),
        "contactability_bucket": bucket,
        "contactability_label": CONTACTABILITY_LABELS[bucket],
        "best_route_type": route_type,
        "best_route_value": route_value,
        "route_candidate_id": candidate_id,
        "rationale": rationale,
        "evidence_summary": evidence,
        "missing_data": list(dict.fromkeys(missing)),
        "next_action": next_action,
        "confidence": confidence,
        "generated_by": generated_by,
        "status": "suggested",
    }
    recommendation["fingerprint"] = _recommendation_fingerprint(recommendation)
    return recommendation
