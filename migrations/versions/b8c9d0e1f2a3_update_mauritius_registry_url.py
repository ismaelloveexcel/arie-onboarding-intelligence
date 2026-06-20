"""Update Mauritius MNS registry links to the current domain.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-06-20
"""

from alembic import op


revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None

_OLD_URL = "https://onlinesearch.mns.mu/"
_NEW_URL = "https://onlinesearch.mns.global/"


def upgrade() -> None:
    op.execute(
        f"UPDATE companies SET verify_url = REPLACE(verify_url, '{_OLD_URL}', '{_NEW_URL}') "
        f"WHERE verify_url LIKE '{_OLD_URL}%'"
    )
    op.execute(
        f"UPDATE introducers SET verify_url = REPLACE(verify_url, '{_OLD_URL}', '{_NEW_URL}') "
        f"WHERE verify_url LIKE '{_OLD_URL}%'"
    )


def downgrade() -> None:
    op.execute(
        f"UPDATE companies SET verify_url = REPLACE(verify_url, '{_NEW_URL}', '{_OLD_URL}') "
        f"WHERE verify_url LIKE '{_NEW_URL}%'"
    )
    op.execute(
        f"UPDATE introducers SET verify_url = REPLACE(verify_url, '{_NEW_URL}', '{_OLD_URL}') "
        f"WHERE verify_url LIKE '{_NEW_URL}%'"
    )
