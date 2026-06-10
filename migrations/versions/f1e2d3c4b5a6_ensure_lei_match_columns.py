"""ensure lei_records has match_state and related columns

Revision ID: f1e2d3c4b5a6
Revises: e7f8a9b0c1d2
Create Date: 2026-06-10

Idempotent guard migration.

The e5f6a7b8c9d0 migration added match_state, confidence_score, match_basis,
and matching_reason to lei_records. However, production databases that were
deployed from the cursor/phase1-risk-hardening-3f07 branch have a divergent
migration path (via e6f7a8b9c0d1) that does not include these columns.

Because e5f6a7b8c9d0 is upstream of the production DB's current head in
master's migration chain, Alembic considers it already applied and will not
re-run it. This migration adds the missing columns unconditionally using
ADD COLUMN IF NOT EXISTS so they are safe to run regardless of prior state.

Also ensures lei_link_review_queue exists (in case the phase1 branch's
review queue table was not applied via this path).
"""

from alembic import op

revision = "f1e2d3c4b5a6"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add LEI match quality columns — idempotent
    op.execute(
        "ALTER TABLE lei_records ADD COLUMN IF NOT EXISTS "
        "match_state TEXT NOT NULL DEFAULT 'UNMATCHED'"
    )
    op.execute(
        "ALTER TABLE lei_records ADD COLUMN IF NOT EXISTS "
        "confidence_score DOUBLE PRECISION NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE lei_records ADD COLUMN IF NOT EXISTS match_basis TEXT"
    )
    op.execute(
        "ALTER TABLE lei_records ADD COLUMN IF NOT EXISTS matching_reason TEXT"
    )

    # Ensure LEI review queue table exists
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
        "CREATE INDEX IF NOT EXISTS idx_lei_link_review_queue_status_created "
        "ON lei_link_review_queue(status, created_at DESC)"
    )


def downgrade() -> None:
    pass  # Non-destructive — do not remove columns on downgrade
