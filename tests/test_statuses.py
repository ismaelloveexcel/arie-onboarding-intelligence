from src.statuses import CANONICAL_STATUSES, normalize_status


def test_normalize_status_keeps_canonical_values():
    assert normalize_status("Reviewing") == "Reviewing"
    assert normalize_status("Not Fit") == "Not Fit"
    assert normalize_status("  Qualified  ") == "Qualified"


def test_normalize_status_maps_compatibility_aliases():
    assert normalize_status("Researching") == "Reviewing"
    assert normalize_status("Outreach Ready") == "Qualified"
    assert normalize_status("Closed — Not Fit") == "Not Fit"
    assert normalize_status("Closed - Not Fit") == "Not Fit"
    assert normalize_status("Client") == "Onboarding"
    assert normalize_status("Opportunity") == "Onboarding"


def test_normalize_status_defaults_to_new_for_blank():
    assert normalize_status("") == "New"
    assert normalize_status(None) == "New"


def test_normalized_output_is_canonical_for_known_aliases():
    aliases = [
        "Researching",
        "Outreach Ready",
        "Closed — Not Fit",
        "Closed - Not Fit",
        "Client",
        "Opportunity",
    ]
    for alias in aliases:
        assert normalize_status(alias) in CANONICAL_STATUSES
