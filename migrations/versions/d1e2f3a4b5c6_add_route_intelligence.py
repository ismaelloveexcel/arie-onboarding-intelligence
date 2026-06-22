"""Add deterministic route intelligence models.

Revision ID: d1e2f3a4b5c6
Revises: c9d0e1f2a3b4
Create Date: 2026-06-21
"""

from alembic import op


revision = "d1e2f3a4b5c6"
down_revision = "f1e2d3c4b5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE route_recommendations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            lead_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            contactability_bucket TEXT NOT NULL CHECK (contactability_bucket IN (
                'ready_to_contact', 'direct_candidate_found',
                'route_via_introducer_csp', 'management_company_route_likely',
                'registry_evidence_only', 'needs_route_research', 'no_usable_route'
            )),
            best_route_type TEXT NOT NULL CHECK (best_route_type IN (
                'direct', 'introducer', 'csp', 'management_company', 'fiduciary',
                'fund_administrator', 'registered_office', 'registry_only',
                'research_required', 'no_usable_route'
            )),
            best_route_value TEXT,
            route_candidate_id UUID,
            rationale TEXT NOT NULL,
            evidence_summary JSONB NOT NULL DEFAULT '[]'::jsonb,
            missing_data JSONB NOT NULL DEFAULT '[]'::jsonb,
            next_action TEXT NOT NULL,
            confidence TEXT NOT NULL CHECK (confidence IN (
                'high', 'medium', 'low', 'not_usable'
            )),
            generated_by TEXT NOT NULL,
            generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            reviewed_by TEXT,
            reviewed_at TIMESTAMPTZ,
            status TEXT NOT NULL DEFAULT 'suggested' CHECK (status IN (
                'suggested', 'accepted', 'rejected', 'superseded'
            )),
            fingerprint TEXT NOT NULL UNIQUE
        )
    """)
    op.execute("""
        CREATE INDEX idx_route_recommendations_lead_status
        ON route_recommendations(lead_id, status, generated_at DESC)
    """)
    op.execute("""
        CREATE INDEX idx_route_recommendations_bucket
        ON route_recommendations(contactability_bucket, status)
    """)

    op.execute("""
        CREATE TABLE introducer_matches (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            lead_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            introducer_id UUID NOT NULL REFERENCES introducers(id) ON DELETE CASCADE,
            match_type TEXT NOT NULL CHECK (match_type IN (
                'exact_name', 'address', 'domain', 'phone', 'email_domain',
                'fsc_category', 'mauritius_finance_member', 'manual',
                'strong_name', 'similar_address'
            )),
            match_strength TEXT NOT NULL CHECK (match_strength IN (
                'high', 'medium', 'low'
            )),
            evidence TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            reviewed_by TEXT,
            reviewed_at TIMESTAMPTZ,
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                'pending', 'accepted', 'rejected'
            )),
            fingerprint TEXT NOT NULL UNIQUE
        )
    """)
    op.execute("""
        CREATE INDEX idx_introducer_matches_lead_status
        ON introducer_matches(lead_id, status, match_strength)
    """)
    op.execute("""
        CREATE INDEX idx_introducer_matches_introducer
        ON introducer_matches(introducer_id, status)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS introducer_matches")
    op.execute("DROP TABLE IF EXISTS route_recommendations")
