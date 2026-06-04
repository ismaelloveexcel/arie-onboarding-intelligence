"""add_person_email_columns

Revision ID: c7d8e9f0a1b2
Revises: a9b8c7d6e5f4
Create Date: 2026-06-04 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, Sequence[str], None] = "a9b8c7d6e5f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE company_officers ADD COLUMN IF NOT EXISTS email TEXT")
    op.execute("ALTER TABLE company_pscs ADD COLUMN IF NOT EXISTS email TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE company_pscs DROP COLUMN IF EXISTS email")
    op.execute("ALTER TABLE company_officers DROP COLUMN IF EXISTS email")
