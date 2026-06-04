"""add_model_version_to_scoring_tables

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-04 16:10:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE score_versions
        ADD COLUMN IF NOT EXISTS model_version TEXT NOT NULL DEFAULT 'deterministic-v1'
        """
    )
    op.execute(
        """
        ALTER TABLE lead_signal_scores
        ADD COLUMN IF NOT EXISTS model_version TEXT NOT NULL DEFAULT 'deterministic-v1'
        """
    )
    op.execute(
        """
        ALTER TABLE score_runs
        ADD COLUMN IF NOT EXISTS model_version TEXT NOT NULL DEFAULT 'deterministic-v1'
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'score_versions_score_version_weights_version_rules_version_key'
            ) THEN
                ALTER TABLE score_versions
                DROP CONSTRAINT score_versions_score_version_weights_version_rules_version_key;
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        ALTER TABLE score_versions
        ADD CONSTRAINT score_versions_version_triplet_unique
        UNIQUE (score_version, weights_version, rules_version, model_version)
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE score_versions
        DROP CONSTRAINT IF EXISTS score_versions_version_triplet_unique
        """
    )
    op.execute(
        """
        ALTER TABLE score_versions
        ADD CONSTRAINT score_versions_score_version_weights_version_rules_version_key
        UNIQUE (score_version, weights_version, rules_version)
        """
    )

    op.execute("ALTER TABLE score_runs DROP COLUMN IF EXISTS model_version")
    op.execute("ALTER TABLE lead_signal_scores DROP COLUMN IF EXISTS model_version")
    op.execute("ALTER TABLE score_versions DROP COLUMN IF EXISTS model_version")
