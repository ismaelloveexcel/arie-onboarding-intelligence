"""phase1 compat statuses and lei review queue

Revision ID: e6f7a8b9c0d1
Revises: d9e8f7a6b5c4
Create Date: 2026-06-02
"""

from typing import Sequence, Union

from alembic import op

revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = "d9e8f7a6b5c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Temporary compatibility window: keep legacy labels accepted while app
    # normalizes writes to canonical statuses.
    op.execute("ALTER TABLE rm_actions DROP CONSTRAINT IF EXISTS rm_actions_status_check")
    op.execute(
        """
        ALTER TABLE rm_actions
        ADD CONSTRAINT rm_actions_status_check CHECK (
            status IN (
                'New',
                'Reviewing',
                'Qualified',
                'Not Relevant',
                'Deferred',
                'Contacted',
                'Onboarding',
                'Not Fit',
                'Researching',
                'Outreach Ready',
                'Opportunity',
                'Client',
                'Closed — Not Fit',
                'Closed - Not Fit'
            )
        )
        """
    )

    op.execute(
        "ALTER TABLE introducer_actions DROP CONSTRAINT IF EXISTS introducer_actions_status_check"
    )
    op.execute(
        """
        ALTER TABLE introducer_actions
        ADD CONSTRAINT introducer_actions_status_check CHECK (
            status IN (
                'New',
                'Reviewing',
                'Qualified',
                'Not Relevant',
                'Deferred',
                'Contacted',
                'Onboarding',
                'Not Fit',
                'Researching',
                'Outreach Ready',
                'Opportunity',
                'Client',
                'Closed — Not Fit',
                'Closed - Not Fit'
            )
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS lei_link_review_queue (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            lei_code TEXT NOT NULL UNIQUE,
            registered_as TEXT,
            legal_name TEXT,
            jurisdiction TEXT,
            match_reason TEXT NOT NULL,
            candidate_company_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'resolved', 'ignored')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_lei_link_review_queue_status_created
        ON lei_link_review_queue(status, created_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_lei_link_review_queue_status_created")
    op.execute("DROP TABLE IF EXISTS lei_link_review_queue")

    op.execute("ALTER TABLE rm_actions DROP CONSTRAINT IF EXISTS rm_actions_status_check")
    op.execute(
        """
        ALTER TABLE rm_actions
        ADD CONSTRAINT rm_actions_status_check CHECK (
            status IN (
                'New',
                'Reviewing',
                'Qualified',
                'Not Relevant',
                'Deferred',
                'Contacted',
                'Onboarding',
                'Not Fit'
            )
        )
        """
    )

    op.execute(
        "ALTER TABLE introducer_actions DROP CONSTRAINT IF EXISTS introducer_actions_status_check"
    )
    op.execute(
        """
        ALTER TABLE introducer_actions
        ADD CONSTRAINT introducer_actions_status_check CHECK (
            status IN (
                'New',
                'Reviewing',
                'Qualified',
                'Not Relevant',
                'Deferred',
                'Contacted',
                'Onboarding',
                'Not Fit'
            )
        )
        """
    )
