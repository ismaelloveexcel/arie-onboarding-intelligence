"""add_pipeline_runs

Revision ID: a1b2c3d4e5f6
Revises: fae29d511bb1
Create Date: 2026-05-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'fae29d511bb1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE pipeline_runs (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at    TIMESTAMPTZ,
            status          TEXT NOT NULL DEFAULT 'running',
            uk_count        INTEGER,
            mu_count        INTEGER,
            scores_count    INTEGER,
            queue_rows      INTEGER,
            duration_seconds NUMERIC(10, 2),
            error           TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_pipeline_runs_started_at ON pipeline_runs(started_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_pipeline_runs_started_at")
    op.execute("DROP TABLE IF EXISTS pipeline_runs")
