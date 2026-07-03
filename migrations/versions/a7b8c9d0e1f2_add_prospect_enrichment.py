"""Add prospect_enrichment table (RM/commercial enrichment layer).

A dedicated, additive enrichment store linked to company_id — it does NOT touch
the core registry/company truth tables and does NOT change scoring. Holds the
externally-researched RM/commercial fields plus full provenance and the raw
imported payload.

Revision ID: a7b8c9d0e1f2
Revises: f4b5c6d7e8f9
Create Date: 2026-06-23
"""

from alembic import op


revision = "a7b8c9d0e1f2"
down_revision = "f4b5c6d7e8f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS prospect_enrichment (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            prospect_quality_grade TEXT
                CHECK (prospect_quality_grade IS NULL
                       OR prospect_quality_grade IN ('A', 'B', 'C', 'D')),
            rm_priority_rank INTEGER,
            prospect_segment TEXT,
            likely_arie_service_need TEXT,
            likely_payment_use_case TEXT,
            business_model_summary TEXT,
            target_buyer_type TEXT,
            suggested_opening_angle TEXT,
            best_contact_route TEXT,
            route_quality TEXT
                CHECK (route_quality IS NULL
                       OR route_quality IN ('high', 'medium', 'low', 'unusable')),
            source_reliability TEXT
                CHECK (source_reliability IS NULL OR source_reliability IN (
                    'official', 'regulator', 'registry', 'reputable_third_party', 'weak')),
            research_status TEXT NOT NULL DEFAULT 'not_researched'
                CHECK (research_status IN (
                    'not_researched', 'researched', 'needs_review', 'ready', 'rejected')),
            last_researched_date DATE,
            next_rm_action TEXT
                CHECK (next_rm_action IS NULL OR next_rm_action IN (
                    'contact_now', 'research_route', 'route_via_introducer', 'hold', 'reject')),
            disqualification_reason TEXT,
            management_shortlist_flag BOOLEAN NOT NULL DEFAULT FALSE,
            route_entry_method TEXT
                CHECK (route_entry_method IS NULL
                       OR route_entry_method IN ('manual', 'import', 'system_detected')),
            checked_by TEXT,
            checked_at TIMESTAMPTZ,
            rm_owner TEXT,
            rm_status TEXT NOT NULL DEFAULT 'not_started'
                CHECK (rm_status IN (
                    'not_started', 'contacted', 'replied', 'meeting', 'won', 'lost', 'not_suitable')),
            last_rm_action_date DATE,
            next_follow_up_date DATE,
            rm_outcome_notes TEXT,
            lost_reason TEXT,
            source_url TEXT,
            source_label TEXT,
            source_type TEXT,
            evidence_summary TEXT,
            ready_to_work BOOLEAN NOT NULL DEFAULT FALSE,
            readiness_bucket TEXT
                CHECK (readiness_bucket IS NULL OR readiness_bucket IN (
                    'ready_to_work', 'research_route', 'hold', 'reject')),
            raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (company_id)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_prospect_enrichment_readiness
        ON prospect_enrichment(readiness_bucket, prospect_quality_grade)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_prospect_enrichment_shortlist
        ON prospect_enrichment(management_shortlist_flag)
        WHERE management_shortlist_flag = TRUE
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_prospect_enrichment_shortlist")
    op.execute("DROP INDEX IF EXISTS idx_prospect_enrichment_readiness")
    op.execute("DROP TABLE IF EXISTS prospect_enrichment")
