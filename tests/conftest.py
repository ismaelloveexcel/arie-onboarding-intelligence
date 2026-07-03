import os

import pytest

from tests.db_safety import configure_database_url_for_pytest


def pytest_configure() -> None:
    try:
        configure_database_url_for_pytest(os.environ)
    except RuntimeError as exc:
        pytest.exit(str(exc), returncode=4)
