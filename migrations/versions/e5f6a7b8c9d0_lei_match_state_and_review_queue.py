"""lei_match_state_and_review_queue

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-04 16:28:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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
    op.execute(
        """
        ALTER TABLE lei_records
        ADD COLUMN IF NOT EXISTS match_basis TEXT
        """
    )
    op.execute(
        """
        ALTER TABLE lei_records
        ADD COLUMN IF NOT EXISTS matching_reason TEXT
        """
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

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS lei_link_review_queue (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            lei_code TEXT NOT NULL UNIQUE REFERENCES lei_records(lei_code) ON DELETE CASCADE,
            registered_as TEXT,
            legal_name TEXT NOT NULL,
            match_basis TEXT,
            confidence_score DOUBLE PRECISION NOT NULL,
            candidate_company_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'resolved')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_lei_link_review_queue_status
        ON lei_link_review_queue(status, updated_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_lei_link_review_queue_status")
    op.execute("DROP TABLE IF EXISTS lei_link_review_queue")

    op.execute("ALTER TABLE lei_records DROP CONSTRAINT IF EXISTS ck_lei_records_confidence_score")
    op.execute("ALTER TABLE lei_records DROP CONSTRAINT IF EXISTS ck_lei_records_match_state")
    op.execute("ALTER TABLE lei_records DROP COLUMN IF EXISTS matching_reason")
    op.execute("ALTER TABLE lei_records DROP COLUMN IF EXISTS match_basis")
    op.execute("ALTER TABLE lei_records DROP COLUMN IF EXISTS confidence_score")
    op.execute("ALTER TABLE lei_records DROP COLUMN IF EXISTS match_state")
