"""add introducers tables

Revision ID: b1c2d3e4f5a7
Revises: fae29d511bb1
Create Date: 2026-01-20 00:00:00.000000

"""
from alembic import op

revision = "b1c2d3e4f5a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS introducers (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_name TEXT NOT NULL,
            normalised_name TEXT NOT NULL,
            jurisdiction TEXT NOT NULL,
            entity_type TEXT,
            incorporation_date DATE,
            source TEXT,
            company_number TEXT,
            file_no TEXT,
            sic_codes TEXT,
            verify_url TEXT,
            contact_email TEXT,
            phone_number TEXT,
            contact_name TEXT,
            address TEXT,
            notes TEXT,
            uploaded_by TEXT,
            raw_data JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (normalised_name, jurisdiction)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_introducers_jurisdiction
        ON introducers(jurisdiction)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_introducers_company_name
        ON introducers(company_name)
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS introducer_actions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            introducer_id UUID NOT NULL REFERENCES introducers(id) ON DELETE CASCADE,
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
            UNIQUE (introducer_id)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS pending_introducer_uploads (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            filename TEXT NOT NULL,
            uploaded_by TEXT NOT NULL,
            row_count INTEGER NOT NULL DEFAULT 0,
            parsed_rows JSONB,
            validation_errors JSONB,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','confirmed','rejected')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS pending_introducer_uploads")
    op.execute("DROP TABLE IF EXISTS introducer_actions")
    op.execute("DROP TABLE IF EXISTS introducers")
