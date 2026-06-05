"""stub: reconcile missing revision from test database

Revision ID: e6f7a8b9c0d1
Revises: e5f6a7b8c9d0
Create Date: 2026-06-05

This is a no-op stub migration that reconciles a revision ID that was applied
to the test database but whose migration file was never committed to the
repository. It exists solely to keep the Alembic revision chain intact.

No schema changes are made.
"""

revision = "e6f7a8b9c0d1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
