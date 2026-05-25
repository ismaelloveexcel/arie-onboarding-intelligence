"""initial_schema

Revision ID: e731bb3a3768
Revises: 
Create Date: 2026-05-25 13:02:38.333112

"""
from typing import Sequence, Union

from alembic import op

revision: str = "e731bb3a3768"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_system TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            company_name TEXT NOT NULL,
            normalised_name TEXT NOT NULL,
            jurisdiction TEXT NOT NULL,
            entity_type TEXT,
            incorporation_date DATE,
            registered_address TEXT,
            sic_codes TEXT[],
            website TEXT,
            verify_url TEXT,
            raw_data JSONB,
            canonical_company_id UUID REFERENCES companies(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(source_system, source_ref)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS lead_scores (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id UUID NOT NULL REFERENCES companies(id),
            score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100),
            tier TEXT NOT NULL CHECK (tier IN ('HIGH','MEDIUM','LOW')),
            reason_codes TEXT[] NOT NULL,
            reason_summary TEXT,
            scoring_version TEXT NOT NULL,
            is_current BOOLEAN NOT NULL DEFAULT TRUE,
            scored_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS queue_snapshot (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            canonical_company_id UUID NOT NULL,
            company_name TEXT NOT NULL,
            jurisdiction TEXT NOT NULL,
            entity_type TEXT,
            incorporation_date DATE,
            verify_url TEXT,
            priority_score INTEGER NOT NULL,
            tier TEXT NOT NULL,
            reason_codes TEXT[] NOT NULL,
            reason_summary TEXT,
            scoring_version TEXT NOT NULL,
            refreshed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_queue_score
        ON queue_snapshot(priority_score DESC)
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS rm_actions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id UUID NOT NULL REFERENCES companies(id),
            assigned_to TEXT,
            status TEXT NOT NULL DEFAULT 'New'
                CHECK (status IN ('New','Reviewing','Qualified',
                                  'Not Relevant','Deferred','Contacted',
                                  'Onboarding','Not Fit')),
            notes TEXT,
            contacted_at TIMESTAMPTZ,
            follow_up_at DATE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(company_id)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            entity_type TEXT NOT NULL,
            entity_id UUID NOT NULL,
            action TEXT NOT NULL,
            actor TEXT NOT NULL,
            old_value JSONB,
            new_value JSONB,
            ip_address TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS merge_lineage (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            from_company_id UUID NOT NULL REFERENCES companies(id),
            to_canonical_id UUID NOT NULL REFERENCES companies(id),
            merged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            merged_by TEXT NOT NULL,
            reverted_at TIMESTAMPTZ,
            field_conflicts JSONB
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS pending_uploads (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            filename TEXT NOT NULL,
            uploaded_by TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            parsed_rows JSONB NOT NULL,
            validation_errors JSONB,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','confirmed','rejected')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            role TEXT NOT NULL CHECK (role IN ('rm','ops','admin')),
            password_hash TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_login TIMESTAMPTZ
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS users")
    op.execute("DROP TABLE IF EXISTS pending_uploads")
    op.execute("DROP TABLE IF EXISTS merge_lineage")
    op.execute("DROP TABLE IF EXISTS audit_log")
    op.execute("DROP TABLE IF EXISTS rm_actions")
    op.execute("DROP INDEX IF EXISTS idx_queue_score")
    op.execute("DROP TABLE IF EXISTS queue_snapshot")
    op.execute("DROP TABLE IF EXISTS lead_scores")
    op.execute("DROP TABLE IF EXISTS companies")
