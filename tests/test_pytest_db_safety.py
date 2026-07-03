import pytest

from tests.db_safety import configure_database_url_for_pytest


def test_pytest_refuses_railway_database_url():
    env = {
        "DATABASE_URL": "postgresql://user:pass@kodama.proxy.rlwy.net:1234/railway",
    }

    with pytest.raises(RuntimeError, match="DATABASE_URL points to Railway"):
        configure_database_url_for_pytest(env)


def test_pytest_uses_test_database_url():
    env = {
        "DATABASE_URL": "postgresql://user:pass@localhost:5432/dev",
        "TEST_DATABASE_URL": "postgresql://user:pass@127.0.0.1:55432/test",
    }

    selected = configure_database_url_for_pytest(env)

    assert selected == "postgresql://user:pass@127.0.0.1:55432/test"
    assert env["DATABASE_URL"] == selected
    assert env["APP_ENV"] == "testing"


def test_pytest_requires_test_database_url_for_db_backed_tests():
    env = {
        "DATABASE_URL": "postgresql://user:pass@127.0.0.1:5432/dev",
    }

    with pytest.raises(RuntimeError, match="must use TEST_DATABASE_URL"):
        configure_database_url_for_pytest(env)
