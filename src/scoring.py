import re
from datetime import date

SCORING_VERSION = "2026.2.0"

DEFAULT_PRIORITY_WEIGHTS = {
    "arie_fit": 0.40,
    "founder": 0.30,
    "lead": 0.20,
    "risk": 0.10,
}

_FINANCIAL_KEYWORDS = {
    "capital",
    "wealth",
    "holdings",
    "fund",
    "partners",
    "asset",
    "invest",
    "investments",
}
_INTERNATIONAL_KEYWORDS = {"international", "global", "offshore"}

_REASON_LABELS = {
    "HOLDING_STRUCTURE": "Holding company structure",
    "INVESTMENT_VEHICLE": "Investment vehicle entity",
    "FUND_STRUCTURE": "Fund entity type",
    "STANDARD_ENTITY": "Standard Ltd/PLC entity",
    "MAURITIUS_GBC": "Mauritius Global Business Company",
    "MAURITIUS_AC": "Mauritius Authorised Company",
    "MAURITIUS_HOLDING_PRESUMED": "Mauritius regulated holding structure (GBC/AC)",
    "UK_ENTITY": "UK-registered entity",
    "FINANCIAL_SIC": "Financial holding SIC (642xx)",
    "FUND_MGMT_SIC": "Fund management SIC (663xx)",
    "FINTECH_SIC": "Fintech/payments SIC (620xx)",
    "FINANCIAL_KEYWORD": "Financial keyword in name",
    "INTERNATIONAL_KEYWORD": "International/global keyword in name",
    "RECENTLY_INCORPORATED": "Recently incorporated (≤ 90 days)",
    "FRESH_LEI": "Fresh LEI registered (≤ 90 days)",
    "HAS_LEI": "Legal Entity Identifier registered",
    "HAS_PSCS": "Beneficial ownership disclosed (≥1 active PSC)",
    "INTERNATIONAL_PSC": "International beneficial owner (cross-border ICP)",
    "INTERNATIONAL_OFFICER": "Non-UK officer / director (cross-border operational signal)",
}

SIGNAL_DETAILS: dict[str, dict] = {
    "HOLDING_STRUCTURE": {
        "label": "Holding Company",
        "points": 25,
        "why": "Set up to hold assets or subsidiaries — typically needs cross-border banking and treasury.",
    },
    "INVESTMENT_VEHICLE": {
        "label": "Investment Vehicle",
        "points": 25,
        "why": "Structured to manage investments — likely needs institutional banking and custody.",
    },
    "FUND_STRUCTURE": {
        "label": "Fund Entity",
        "points": 20,
        "why": "Fund structure — likely needs banking, custody and compliance infrastructure.",
    },
    "STANDARD_ENTITY": {
        "label": "UK Limited Company",
        "points": 10,
        "why": "Standard UK company — meets the baseline profile.",
    },
    "UK_ENTITY": {
        "label": "UK Registered",
        "points": 10,
        "why": "Registered in the UK — our primary market.",
    },
    "MAURITIUS_GBC": {
        "label": "Mauritius Holding Company (GBC)",
        "points": 30,
        "why": "Mauritius holding structure designed for cross-border investment — one of our strongest prospect types.",
    },
    "MAURITIUS_AC": {
        "label": "Mauritius Authorised Company (AC)",
        "points": 20,
        "why": "Cross-border Mauritius entity with a lighter regulatory footprint.",
    },
    "MAURITIUS_HOLDING_PRESUMED": {
        "label": "",
        "points": None,
        "why": "",
        "hidden": True,
    },
    "FINANCIAL_SIC": {
        "label": "Financial Holding (Industry Code)",
        "points": 20,
        "why": "Industry code shows holding company activities — directly in our target segment.",
    },
    "FUND_MGMT_SIC": {
        "label": "Fund Management (Industry Code)",
        "points": 20,
        "why": "Industry code shows fund management activity — institutional banking prospect.",
    },
    "FINTECH_SIC": {
        "label": "Fintech / Payments (Industry Code)",
        "points": 15,
        "why": "Industry code indicates fintech or payments activity.",
    },
    "FINANCIAL_KEYWORD": {
        "label": "Financial Name",
        "points": 10,
        "why": "Company name includes a financial or investment word (capital, fund, wealth, asset, etc.).",
    },
    "INTERNATIONAL_KEYWORD": {
        "label": "International Name",
        "points": 5,
        "why": "Company name includes 'international' or 'global' — suggests cross-border operations.",
    },
    "RECENTLY_INCORPORATED": {
        "label": "Newly Formed (< 90 days)",
        "points": 5,
        "why": "Just incorporated — likely still choosing their bank. Good time to make contact.",
    },
    "FRESH_LEI": {
        "label": "Just Registered an LEI",
        "points": 30,
        "why": "Registered a Legal Entity Identifier in the last 90 days — actively opening regulated accounts right now.",
    },
    "HAS_LEI": {
        "label": "Has an LEI",
        "points": 15,
        "why": "Has a Legal Entity Identifier — already working with regulated financial counterparties.",
    },
    "HAS_PSCS": {
        "label": "Owners on Record",
        "points": 5,
        "why": "Beneficial owners are registered — transparent ownership makes onboarding easier.",
    },
    "INTERNATIONAL_PSC": {
        "label": "International Owner",
        "points": 10,
        "why": "Beneficial owner is based outside the UK — confirms cross-border structure.",
    },
    "INTERNATIONAL_OFFICER": {
        "label": "International Director",
        "points": 10,
        "why": "Active director based outside the UK — signals cross-border operational needs.",
    },
}

_UK_COUNTRIES = {
    "",
    "GB",
    "UK",
    "UNITED KINGDOM",
    "ENGLAND",
    "SCOTLAND",
    "WALES",
    "NORTHERN IRELAND",
}

_UK_NATIONALITIES = {
    "",
    "BRITISH",
    "ENGLISH",
    "SCOTTISH",
    "WELSH",
    "NORTHERN IRISH",
}


def _name_words(name: str) -> set[str]:
    return set(re.findall(r"[a-z]+", name.lower()))


def calculate_score(
    company: dict,
    lei: dict | None = None,
    pscs: list[dict] | None = None,
    officers: list[dict] | None = None,
) -> tuple[int, list[str], str]:
    """
    Pure function. No DB access. No API calls.
    Returns (score 0-100, reason_codes, tier).
    """
    score = 0
    codes: list[str] = []

    entity_type = (company.get("entity_type") or "").lower().strip()
    jurisdiction = (company.get("jurisdiction") or "").strip()
    sic_codes = company.get("sic_codes") or []
    name = company.get("company_name") or ""
    words = _name_words(name)

    # --- Entity type ---
    if "holding" in entity_type:
        score += 25
        codes.append("HOLDING_STRUCTURE")
    elif "investment" in entity_type or "vehicle" in entity_type:
        score += 25
        codes.append("INVESTMENT_VEHICLE")
    elif "fund" in entity_type:
        score += 20
        codes.append("FUND_STRUCTURE")
    elif entity_type in {
        "ltd",
        "limited",
        "plc",
        "private limited company",
        "public limited company",
        "private limited",
    }:
        score += 10
        codes.append("STANDARD_ENTITY")

    # --- Jurisdiction ---
    if jurisdiction == "UK":
        score += 10
        codes.append("UK_ENTITY")
    elif jurisdiction == "Mauritius":
        et = entity_type
        if "gbc" in et or "global business" in et:
            score += 30
            codes.append("MAURITIUS_GBC")
            score += 25
            codes.append("MAURITIUS_HOLDING_PRESUMED")
        elif "authorised" in et or et == "ac":
            score += 20
            codes.append("MAURITIUS_AC")
            score += 15
            codes.append("MAURITIUS_HOLDING_PRESUMED")

    # --- SIC codes (each family scored independently, once each) ---
    sic_strs = [str(s).strip() for s in sic_codes]
    if any(s.startswith("642") for s in sic_strs):
        score += 20
        codes.append("FINANCIAL_SIC")
    if any(s.startswith("663") for s in sic_strs):
        score += 20
        codes.append("FUND_MGMT_SIC")
    if any(s.startswith("620") for s in sic_strs):
        score += 15
        codes.append("FINTECH_SIC")

    # --- Recently incorporated ---
    from datetime import date as _date

    inc_date = company.get("incorporation_date")
    if inc_date is not None:
        try:
            age_days = (_date.today() - inc_date).days
            if age_days <= 90:
                score += 5
                codes.append("RECENTLY_INCORPORATED")
        except Exception:
            pass

    # --- LEI ---
    if lei is not None:
        days = lei.get("days_since_registration")
        if days is not None and days <= 90:
            score += 30
            codes.append("FRESH_LEI")
        else:
            score += 15
            codes.append("HAS_LEI")

    # --- Name keywords ---
    if words & _FINANCIAL_KEYWORDS:
        score += 10
        codes.append("FINANCIAL_KEYWORD")
    if words & _INTERNATIONAL_KEYWORDS:
        score += 5
        codes.append("INTERNATIONAL_KEYWORD")

    # --- PSCs (beneficial owners) ---
    if pscs:
        active = [p for p in pscs if p.get("ceased_on") is None]
        if active:
            score += 5
            codes.append("HAS_PSCS")
            if any(
                (p.get("country_of_residence") or "").strip().upper()
                not in _UK_COUNTRIES
                for p in active
            ):
                score += 10
                codes.append("INTERNATIONAL_PSC")

    # --- Officers (non-UK nationality) ---
    if officers:
        active_officers = [o for o in officers if o.get("resigned_on") is None]
        if any(
            (o.get("nationality") or "").strip().upper() not in _UK_NATIONALITIES
            for o in active_officers
            if (o.get("nationality") or "").strip()
        ):
            score += 10
            codes.append("INTERNATIONAL_OFFICER")

    score = min(score, 100)

    if score >= 70:
        tier = "HIGH"
    elif score >= 40:
        tier = "MEDIUM"
    else:
        tier = "LOW"

    return score, codes, tier


def build_reason_summary(codes: list[str]) -> str:
    if not codes:
        return "No priority signals matched."

    parts = []
    lei_phrase = None

    for code in codes:
        if code == "FRESH_LEI":
            lei_phrase = (
                "with a fresh LEI registered — strong " "onboarding-readiness signal"
            )
        elif code == "HAS_LEI":
            lei_phrase = "with an active Legal Entity Identifier"
        else:
            label = _REASON_LABELS.get(code)
            if label:
                parts.append(label)

    if not parts and not lei_phrase:
        return "No priority signals matched."

    base = "; ".join(parts) if parts else "Entity identified"
    if lei_phrase:
        return f"{base}, {lei_phrase}."
    return f"{base}."


# ---------------------------------------------------------------------------
# PR 1: dimensional scores feeding the new priority_score
# ---------------------------------------------------------------------------

_FIT_FINANCIAL_SIC_PREFIXES = ("642", "663", "620", "649", "661", "662", "643", "651")
_FIT_TRADE_SIC_PREFIXES = ("46", "47", "493", "521", "522", "461", "462", "463", "464")

_CORE_CORRIDOR_JURISDICTIONS = {
    "UK",
    "GB",
    "UAE",
    "AE",
    "MAURITIUS",
    "MU",
    "HK",
    "HONG KONG",
    "SG",
    "SINGAPORE",
}


def calculate_arie_fit(company: dict) -> tuple[int, list[str]]:
    """Pure ICP fit signal — does the entity look like an Arie target?
    Returns (score 0-100, reason_codes).
    """
    score = 0
    codes: list[str] = []

    entity_type = (company.get("entity_type") or "").lower().strip()
    jurisdiction = (company.get("jurisdiction") or "").strip()
    sic_strs = [str(s).strip() for s in (company.get("sic_codes") or [])]

    if "holding" in entity_type:
        score += 40
        codes.append("FIT_HOLDING")
    elif "investment" in entity_type or "vehicle" in entity_type:
        score += 40
        codes.append("FIT_INVESTMENT_VEHICLE")
    elif "fund" in entity_type:
        score += 35
        codes.append("FIT_FUND")
    elif entity_type in {
        "ltd",
        "limited",
        "plc",
        "private limited company",
        "public limited company",
        "private limited",
    }:
        score += 10
        codes.append("FIT_STANDARD_ENTITY")

    if jurisdiction == "Mauritius":
        if "gbc" in entity_type or "global business" in entity_type:
            score += 35
            codes.append("FIT_MAURITIUS_GBC")
        elif "authorised" in entity_type or entity_type == "ac":
            score += 25
            codes.append("FIT_MAURITIUS_AC")
        else:
            score += 15
            codes.append("FIT_MAURITIUS_OTHER")
    elif jurisdiction == "UK":
        score += 5
        codes.append("FIT_UK_BASE")

    if any(s.startswith(p) for s in sic_strs for p in _FIT_FINANCIAL_SIC_PREFIXES):
        score += 30
        codes.append("FIT_FINANCIAL_SIC")
    if any(s.startswith(p) for s in sic_strs for p in _FIT_TRADE_SIC_PREFIXES):
        score += 20
        codes.append("FIT_TRADE_SIC")

    return min(score, 100), codes


def calculate_freshness(
    incorporation_date: date | None, *, today: date | None = None
) -> int:
    """Newer companies are more valuable — they're still picking a bank."""
    if incorporation_date is None:
        return 0
    today = today or date.today()
    try:
        age_days = (today - incorporation_date).days
    except Exception:
        return 0
    if age_days < 0:
        return 0
    if age_days <= 7:
        return 100
    if age_days <= 30:
        return 80
    if age_days <= 90:
        return 50
    if age_days <= 180:
        return 25
    return 10


def calculate_keyword_score(
    name: str, keywords: list[dict] | None
) -> tuple[int, list[str]]:
    """Name-keyword score driven by the lead_keywords config table.
    `keywords` rows: {term, polarity, weight}. Returns (0-100, matched terms).
    """
    if not name or not keywords:
        return 50, []  # neutral when config empty
    words = _name_words(name)
    score = 50
    matched: list[str] = []
    for kw in keywords:
        term = (kw.get("term") or "").lower().strip()
        if not term:
            continue
        polarity = kw.get("polarity") or "positive"
        weight = int(kw.get("weight") or 0)
        if term in words or term in name.lower():
            if polarity == "positive":
                score += weight
                matched.append(f"+{term}")
            else:
                score -= weight
                matched.append(f"-{term}")
    return max(0, min(score, 100)), matched


def derive_reachability_status(
    *, website: str | None, has_email: bool, linkedin_url: str | None
) -> str:
    """Status enum: ready_outreach / research_required / no_contact_path."""
    web = bool((website or "").strip())
    li = bool((linkedin_url or "").strip())
    channels = sum([web, has_email, li])
    if channels >= 2:
        return "ready_outreach"
    if channels == 1:
        return "research_required"
    return "no_contact_path"


def derive_lead_readiness(
    *,
    arie_fit_score: int,
    reachability_status: str,
    rm_status: str | None,
) -> str:
    """Stage enum: discovered / qualified / contactable / engaged."""
    engaged_statuses = {"Contacted", "Onboarding", "Qualified"}
    if rm_status in engaged_statuses:
        return "engaged"
    if arie_fit_score >= 60 and reachability_status == "ready_outreach":
        return "contactable"
    if arie_fit_score >= 60:
        return "qualified"
    return "discovered"


def derive_enrichment_tier(priority_score: int) -> str:
    """Tiered vendor gating: A top 5% (on-demand), B top 20% (nightly), C never."""
    if priority_score >= 80:
        return "A"
    if priority_score >= 60:
        return "B"
    return "C"


def derive_next_action(
    *,
    rm_status: str | None,
    reachability_status: str,
    has_introducer: bool = False,
) -> str:
    """Deterministic, rule-based next-step hint for the RM. No AI, no scoring.
    Mirrors the simple decision flow agreed for the pilot."""
    status = (rm_status or "New").strip()
    if status == "Client":
        return "Won — active client"
    if status == "Closed — Not Fit":
        return "Deprioritise / Not fit"
    if status in {"Contacted", "Opportunity"}:
        return "Follow up on the conversation"
    if has_introducer:
        return "Review introducer route"
    if reachability_status == "ready_outreach":
        return "Ready for outreach"
    if reachability_status == "no_contact_path":
        return "Research company / find a contact first"
    return "Research company / contact first"


def contact_path_label(reachability_status: str) -> str:
    """Map the deterministic reachability enum to a Ready/Partial/Missing badge."""
    return {
        "ready_outreach": "Ready",
        "research_required": "Partial",
        "no_contact_path": "Missing",
    }.get(reachability_status, "Partial")


def compute_priority_score(
    *,
    arie_fit_score: int,
    founder_quality_score: int,
    freshness_score: int,
    keyword_score: int,
    cross_border_score: int,
    risk_score: int,
    weights: dict | None = None,
) -> int:
    """Weighted combination. `lead` = average of freshness, keyword, cross_border."""
    w = {**DEFAULT_PRIORITY_WEIGHTS, **(weights or {})}
    lead_component = (freshness_score + keyword_score + cross_border_score) / 3.0
    raw = (
        w["arie_fit"] * arie_fit_score
        + w["founder"] * founder_quality_score
        + w["lead"] * lead_component
        + w["risk"] * risk_score
    )
    return max(0, min(100, int(round(raw))))


def build_why_reasons(
    *,
    company: dict,
    arie_fit_score: int,
    freshness_score: int,
    reachability_status: str,
    has_website: bool,
    has_email: bool,
    has_linkedin: bool,
    lei: dict | None,
    officers_count: int,
) -> list[str]:
    """Deterministic bullet list shown above the narrative."""
    bullets: list[str] = []
    inc = company.get("incorporation_date")
    if inc:
        try:
            age = (date.today() - inc).days
            if age <= 7:
                bullets.append(f"Incorporated {age} day{'s' if age != 1 else ''} ago")
            elif age <= 30:
                bullets.append(f"Incorporated {age} days ago")
            elif age <= 90:
                bullets.append("Incorporated within last 90 days")
        except Exception:
            pass

    sic_strs = [str(s).strip() for s in (company.get("sic_codes") or [])]
    if any(s.startswith(p) for s in sic_strs for p in _FIT_FINANCIAL_SIC_PREFIXES):
        bullets.append("Financial / fund-management SIC code")
    if any(s.startswith(p) for s in sic_strs for p in _FIT_TRADE_SIC_PREFIXES):
        bullets.append("Import / export / trading SIC code")

    entity_type = (company.get("entity_type") or "").lower()
    if "holding" in entity_type:
        bullets.append("Holding-company structure")
    elif "fund" in entity_type:
        bullets.append("Fund structure")

    if has_website:
        bullets.append("Website discovered")
    if has_email:
        bullets.append("Contact email on file")
    if has_linkedin:
        bullets.append("LinkedIn page found")

    if lei:
        if (
            lei.get("days_since_registration") is not None
            and lei["days_since_registration"] <= 90
        ):
            bullets.append("Fresh LEI (active onboarding)")
        else:
            bullets.append("Legal Entity Identifier registered")

    if officers_count >= 1:
        bullets.append(
            f"{officers_count} active director{'s' if officers_count != 1 else ''} on record"
        )

    if arie_fit_score >= 70:
        bullets.append("Arie Fit: High")
    elif arie_fit_score >= 40:
        bullets.append("Arie Fit: Medium")

    if reachability_status == "ready_outreach":
        bullets.append("Ready for outreach")

    return bullets


def load_priority_weights(conn) -> dict:
    """Pull current weights from scoring_weights config table, else defaults."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT weights FROM scoring_weights WHERE id = 'priority'")
            row = cur.fetchone()
            if row and row[0]:
                return {**DEFAULT_PRIORITY_WEIGHTS, **row[0]}
    except Exception:
        pass
    return dict(DEFAULT_PRIORITY_WEIGHTS)


def load_lead_keywords(conn) -> list[dict]:
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT term, polarity, weight FROM lead_keywords WHERE active = TRUE"
            )
            return [
                {"term": r[0], "polarity": r[1], "weight": r[2]} for r in cur.fetchall()
            ]
    except Exception:
        return []
