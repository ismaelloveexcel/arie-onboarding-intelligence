import re

SCORING_VERSION = "2025.1.4"

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


def _name_words(name: str) -> set[str]:
    return set(re.findall(r"[a-z]+", name.lower()))


def calculate_score(
    company: dict,
    lei: dict | None = None,
    pscs: list[dict] | None = None,
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
