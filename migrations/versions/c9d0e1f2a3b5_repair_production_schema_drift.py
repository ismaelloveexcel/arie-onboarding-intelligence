"""repair production schema drift for status-adjacent health columns

Revision ID: c9d0e1f2a3b5
Revises: b8c9d0e1f2a3
Create Date: 2026-07-03

Production was stamped past earlier idempotent repair migrations, but two
column groups were still absent. This migration reasserts those schema effects
after the current head without changing product workflow.
"""

from alembic import op


revision = "c9d0e1f2a3b5"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE companies
        ADD COLUMN IF NOT EXISTS score_state TEXT NOT NULL DEFAULT 'unscored'
        """
    )
    op.execute(
        """
        ALTER TABLE companies
        ADD COLUMN IF NOT EXISTS score_state_reason TEXT
        """
    )
    op.execute(
        """
        ALTER TABLE companies
        ADD COLUMN IF NOT EXISTS score_state_updated_at TIMESTAMPTZ
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'ck_companies_score_state'
            ) THEN
                ALTER TABLE companies
                ADD CONSTRAINT ck_companies_score_state
                CHECK (score_state IN ('unscored', 'scored', 'failed'));
            END IF;
        END $$;
        """
    )

    op.execute(
        """
        ALTER TABLE lei_records
        ADD COLUMN IF NOT EXISTS match_state TEXT NOT NULL DEFAULT 'UNMATCHED'
        """
    )
    op.execute(
        """
        ALTER TABLE lei_records
        ADD COLUMN IF NOT EXISTS confidence_score DOUBLE PRECISION NOT NULL DEFAULT 0
        """
    )
    op.execute("ALTER TABLE lei_records ADD COLUMN IF NOT EXISTS match_basis TEXT")
    op.execute(
        "ALTER TABLE lei_records ADD COLUMN IF NOT EXISTS matching_reason TEXT"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'ck_lei_records_match_state'
            ) THEN
                ALTER TABLE lei_records
                ADD CONSTRAINT ck_lei_records_match_state
                CHECK (match_state IN ('UNMATCHED', 'AMBIGUOUS', 'VERIFIED'));
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'ck_lei_records_confidence_score'
            ) THEN
                ALTER TABLE lei_records
                ADD CONSTRAINT ck_lei_records_confidence_score
                CHECK (confidence_score >= 0 AND confidence_score <= 1);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    pass
