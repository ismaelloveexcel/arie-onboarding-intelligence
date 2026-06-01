import re

SCORING_VERSION = "2026.1.0"

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
