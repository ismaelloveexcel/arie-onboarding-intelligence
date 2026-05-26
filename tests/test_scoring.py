"""
Fixture tests for the scoring engine.
Every test uses a fixed, fully-specified input and asserts exact outputs.
Changing a score intentionally means updating the fixture here.
"""
from src.scoring import SCORING_VERSION, build_reason_summary, calculate_score


# ---------------------------------------------------------------------------
# Fixture 1: UK holding company with financial SIC and name keywords
# Signals: HOLDING_STRUCTURE(+25) UK_ENTITY(+10) FINANCIAL_SIC(+20)
#          FINANCIAL_KEYWORD(+10) INTERNATIONAL_KEYWORD(+5) = 70
# ---------------------------------------------------------------------------
def test_uk_holding_company_is_high():
    company = {
        "company_name": "Global Capital Holdings Ltd",
        "jurisdiction": "UK",
        "entity_type": "holding company",
        "sic_codes": ["64200"],
    }
    score, codes, tier = calculate_score(company)
    assert score == 70
    assert tier == "HIGH"
    assert "HOLDING_STRUCTURE" in codes
    assert "UK_ENTITY" in codes
    assert "FINANCIAL_SIC" in codes
    assert "FINANCIAL_KEYWORD" in codes
    assert "INTERNATIONAL_KEYWORD" in codes


# ---------------------------------------------------------------------------
# Fixture 2: Generic UK Ltd — no SIC codes, no financial keywords
# Signals: STANDARD_ENTITY(+10) UK_ENTITY(+10) = 20
# ---------------------------------------------------------------------------
def test_generic_uk_ltd_is_low():
    company = {
        "company_name": "Smith And Jones Trading Limited",
        "jurisdiction": "UK",
        "entity_type": "private limited company",
        "sic_codes": [],
    }
    score, codes, tier = calculate_score(company)
    assert score == 20
    assert tier == "LOW"
    assert "STANDARD_ENTITY" in codes
    assert "UK_ENTITY" in codes
    assert "HOLDING_STRUCTURE" not in codes


# ---------------------------------------------------------------------------
# Fixture 3: Mauritius GBC with fund SIC and financial keywords
# Signals: MAURITIUS_GBC(+20) FUND_MGMT_SIC(+20) FINANCIAL_KEYWORD(+10) = 50
# ---------------------------------------------------------------------------
def test_mauritius_gbc_with_fund_sic_is_medium():
    company = {
        "company_name": "Sunrise Capital Partners GBC",
        "jurisdiction": "Mauritius",
        "entity_type": "Global Business Company",
        "sic_codes": ["66300"],
    }
    score, codes, tier = calculate_score(company)
    assert score == 50
    assert tier == "MEDIUM"
    assert "MAURITIUS_GBC" in codes
    assert "FUND_MGMT_SIC" in codes
    assert "FINANCIAL_KEYWORD" in codes


# ---------------------------------------------------------------------------
# Fixture 4: Mauritius Authorised Company — no SIC, no keywords
# Signals: MAURITIUS_AC(+15) = 15
# ---------------------------------------------------------------------------
def test_mauritius_ac_no_signals_is_low():
    company = {
        "company_name": "Bayside Services AC",
        "jurisdiction": "Mauritius",
        "entity_type": "Authorised Company",
        "sic_codes": [],
    }
    score, codes, tier = calculate_score(company)
    assert score == 15
    assert tier == "LOW"
    assert "MAURITIUS_AC" in codes
    assert "MAURITIUS_GBC" not in codes


# ---------------------------------------------------------------------------
# Fixture 5: UK investment vehicle with fund SIC + keywords
# Signals: INVESTMENT_VEHICLE(+25) UK_ENTITY(+10) FUND_MGMT_SIC(+20)
#          FINANCIAL_KEYWORD(+10) INTERNATIONAL_KEYWORD(+5) = 70
# ---------------------------------------------------------------------------
def test_uk_investment_vehicle_is_high():
    company = {
        "company_name": "Atlantic Fund Partners International",
        "jurisdiction": "UK",
        "entity_type": "investment vehicle",
        "sic_codes": ["66300"],
    }
    score, codes, tier = calculate_score(company)
    assert score == 70
    assert tier == "HIGH"
    assert "INVESTMENT_VEHICLE" in codes
    assert "UK_ENTITY" in codes
    assert "FUND_MGMT_SIC" in codes
    assert "FINANCIAL_KEYWORD" in codes
    assert "INTERNATIONAL_KEYWORD" in codes


# ---------------------------------------------------------------------------
# Fixture 6: Score cap enforced at 100
# Signals: HOLDING_STRUCTURE(+25) UK_ENTITY(+10) FINANCIAL_SIC(+20)
#          FUND_MGMT_SIC(+20) FINTECH_SIC(+15) FINANCIAL_KEYWORD(+10)
#          INTERNATIONAL_KEYWORD(+5) = 105 → capped at 100
# ---------------------------------------------------------------------------
def test_score_capped_at_100():
    company = {
        "company_name": "Global Capital Holdings International",
        "jurisdiction": "UK",
        "entity_type": "holding company",
        "sic_codes": ["64200", "66300", "62000"],
    }
    score, codes, tier = calculate_score(company)
    assert score == 100
    assert tier == "HIGH"
    assert len(codes) == 7


# ---------------------------------------------------------------------------
# Fixture 7: Determinism — identical input always produces identical output
# ---------------------------------------------------------------------------
def test_same_input_same_output():
    company = {
        "company_name": "Trident Asset Management Ltd",
        "jurisdiction": "UK",
        "entity_type": "private limited company",
        "sic_codes": ["66300"],
    }
    result_a = calculate_score(company)
    result_b = calculate_score(company)
    assert result_a == result_b


# ---------------------------------------------------------------------------
# Fixture 8: Empty / null fields — must not raise, must return LOW
# ---------------------------------------------------------------------------
def test_empty_company_does_not_raise():
    company = {
        "company_name": "",
        "jurisdiction": "",
        "entity_type": None,
        "sic_codes": None,
    }
    score, codes, tier = calculate_score(company)
    assert score == 0
    assert tier == "LOW"
    assert codes == []


# ---------------------------------------------------------------------------
# Fixture 9: SCORING_VERSION is the canonical value
# ---------------------------------------------------------------------------
def test_scoring_version():
    assert SCORING_VERSION == "2025.1.1"


# ---------------------------------------------------------------------------
# Fixture 10: reason_summary is non-empty when codes present
# ---------------------------------------------------------------------------
def test_reason_summary_populated():
    _, codes, _ = calculate_score({
        "company_name": "Meridian Wealth Partners",
        "jurisdiction": "UK",
        "entity_type": "holding company",
        "sic_codes": ["64200"],
    })
    summary = build_reason_summary(codes)
    assert len(summary) > 0
    assert "Holding" in summary
