from scripts.portal_bootstrap import missing_env_vars


def test_missing_env_vars_reports_only_missing_items():
    env = {
        "DATABASE_URL": "postgresql://example",
        "COMPANIES_HOUSE_API_KEY": "",
        "SECRET_KEY": "secret",
    }
    missing = missing_env_vars(
        ("DATABASE_URL", "COMPANIES_HOUSE_API_KEY", "SECRET_KEY", "RM_NAMES"),
        environ=env,
    )
    assert missing == ["COMPANIES_HOUSE_API_KEY", "RM_NAMES"]
