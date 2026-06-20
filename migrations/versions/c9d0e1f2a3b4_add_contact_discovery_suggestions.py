"""Add review-gated contact discovery suggestions.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-06-20
"""

from alembic import op


revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE contact_discovery_suggestions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            suggestion_type TEXT NOT NULL CHECK (suggestion_type IN (
                'website', 'contact_page', 'generic_email', 'company_linkedin',
                'registry', 'regulator', 'csp_route', 'introducer_route'
            )),
            suggested_value TEXT NOT NULL,
            source_name TEXT NOT NULL,
            source_url TEXT NOT NULL,
            search_query TEXT,
            confidence TEXT NOT NULL CHECK (confidence IN ('Low', 'Medium', 'High')),
            confidence_reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Needs Review'
                CHECK (status IN ('Needs Review', 'Accepted', 'Rejected')),
            discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            reviewed_by TEXT,
            reviewed_at TIMESTAMPTZ,
            notes TEXT,
            fingerprint TEXT NOT NULL UNIQUE
        )
    """)
    op.execute("""
        CREATE INDEX idx_contact_discovery_company_status
        ON contact_discovery_suggestions(company_id, status, discovered_at DESC)
    """)
    op.execute("""
        CREATE INDEX idx_contact_discovery_status_type
        ON contact_discovery_suggestions(status, suggestion_type)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS contact_discovery_suggestions")
