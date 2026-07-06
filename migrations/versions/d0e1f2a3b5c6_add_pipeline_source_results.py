"""add pipeline source-level result tracking

Revision ID: d0e1f2a3b5c6
Revises: c9d0e1f2a3b5
Create Date: 2026-07-06
"""

from alembic import op


revision = "d0e1f2a3b5c6"
down_revision = "c9d0e1f2a3b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE pipeline_runs ADD COLUMN IF NOT EXISTS lei_count INTEGER")
    op.execute(
        """
        ALTER TABLE pipeline_runs
        ADD COLUMN IF NOT EXISTS source_results JSONB NOT NULL DEFAULT '[]'::jsonb
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE pipeline_runs DROP COLUMN IF EXISTS source_results")
    op.execute("ALTER TABLE pipeline_runs DROP COLUMN IF EXISTS lei_count")
