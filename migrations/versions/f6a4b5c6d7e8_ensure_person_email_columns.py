"""re-ensure company_officers/company_pscs.email columns exist

The original migration c7d8e9f0a1b2 adds these columns, but on the production
database it was recorded as applied without the columns actually existing (a
prior merge-head / stamp reconciliation). The app previously masked this with a
request-path ALTER TABLE guard, which has been removed in favour of migrations.
This fresh, idempotent revision runs at the current head so it executes on every
environment regardless of the recorded history, and is a no-op where the columns
already exist.

Revision ID: f6a4b5c6d7e8
Revises: f5c3d4e5f6a7
Create Date: 2026-06-17
"""

from alembic import op

revision = "f6a4b5c6d7e8"
down_revision = "f5c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE company_officers ADD COLUMN IF NOT EXISTS email TEXT")
    op.execute("ALTER TABLE company_pscs ADD COLUMN IF NOT EXISTS email TEXT")


def downgrade() -> None:
    # No-op: do not drop columns that pre-existed this revision's purpose.
    pass
