import pytest

from src.domain.statuses import CANONICAL_STATUSES, normalize_status, require_canonical_status, status_options


def test_status_mapping_valid_values():
    values = {item["value"] for item in status_options()}
    assert values == set(CANONICAL_STATUSES)
    for value in values:
        assert require_canonical_status(value) == value


def test_status_rejects_unknown():
    with pytest.raises(ValueError):
        require_canonical_status("definitely_unknown_status")


def test_legacy_alias_mapping():
    assert normalize_status("Researching") == "reviewing"
    assert normalize_status("Outreach Ready") == "qualified"
    assert normalize_status("Opportunity") == "onboarding"
    assert normalize_status("Closed — Not Fit") == "not_fit"
