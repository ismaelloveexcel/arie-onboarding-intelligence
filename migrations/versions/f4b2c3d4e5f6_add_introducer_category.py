"""add introducers.category for lean introducer intelligence

Adds a single nullable category column so RMs can classify an introducer
(management company / CSP / fiduciary / referral partner / other) without
turning the module into a partner CRM. Relationship owner and status already
exist via introducer_actions.

Revision ID: f4b2c3d4e5f6
Revises: f3a1b2c3d4e5
Create Date: 2026-06-17
"""

from alembic import op

revision = "f4b2c3d4e5f6"
down_revision = "f3a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE introducers ADD COLUMN IF NOT EXISTS category TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE introducers DROP COLUMN IF EXISTS category")
