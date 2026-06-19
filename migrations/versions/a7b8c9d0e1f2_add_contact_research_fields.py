"""add contact research fields

Revision ID: a7b8c9d0e1f2
Revises: f6a4b5c6d7e8
Create Date: 2026-06-19
"""

from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "f6a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE company_contacts ADD COLUMN IF NOT EXISTS contact_form_url TEXT")
    op.execute("ALTER TABLE company_contacts ADD COLUMN IF NOT EXISTS checked_by TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE company_contacts DROP COLUMN IF EXISTS checked_by")
    op.execute("ALTER TABLE company_contacts DROP COLUMN IF EXISTS contact_form_url")
