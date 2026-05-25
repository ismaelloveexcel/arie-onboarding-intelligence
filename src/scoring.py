import re

SCORING_VERSION = "2025.1.0"

_FINANCIAL_KEYWORDS = {"capital", "wealth", "holdings", "fund", "partners", "asset", "invest", "investments"}
_INTERNATIONAL_KEYWORDS = {"international", "global", "offshore"}

_REASON_LABELS = {
    "HOLDING_STRUCTURE":    "Holding company structure",
    "INVESTMENT_VEHICLE":   "Investment vehicle entity",
    "FUND_STRUCTURE":       "Fund entity type",
    "STANDARD_ENTITY":      "Standard Ltd/PLC entity",
    "MAURITIUS_GBC":        "Mauritius Global Business Company",
    "MAURITIUS_AC":         "Mauritius Authorised Company",
    "UK_ENTITY":            "UK-registered entity",
    "FINANCIAL_SIC":        "Financial holding SIC (642xx)",
    "FUND_MGMT_SIC":        "Fund management SIC (663xx)",
    "FINTECH_SIC":          "Fintech/payments SIC (620xx)",
    "FINANCIAL_KEYWORD":    "Financial keyword in name",
    "INTERNATIONAL_KEYWORD": "International/global keyword in name",
}


def _name_words(name: str) -> set[str]:
    return set(re.findall(r"[a-z]+", name.lower()))


def calculate_score(company: dict) -> tuple[int, list[str], str]:
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
        "ltd", "limited", "plc", "private limited company",
        "public limited company", "private limited",
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
            score += 20
            codes.append("MAURITIUS_GBC")
        elif "authorised" in et or et == "ac":
            score += 15
            codes.append("MAURITIUS_AC")

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

    # --- Name keywords ---
    if words & _FINANCIAL_KEYWORDS:
        score += 10
        codes.append("FINANCIAL_KEYWORD")
    if words & _INTERNATIONAL_KEYWORDS:
        score += 5
        codes.append("INTERNATIONAL_KEYWORD")

    score = min(score, 100)

    if score >= 70:
        tier = "HIGH"
    elif score >= 40:
        tier = "MEDIUM"
    else:
        tier = "LOW"

    return score, codes, tier


def build_reason_summary(codes: list[str]) -> str:
    """Human-readable one-liner from matched reason codes."""
    if not codes:
        return "No priority signals matched."
    labels = [_REASON_LABELS.get(c, c) for c in codes]
    return "; ".join(labels) + "."
