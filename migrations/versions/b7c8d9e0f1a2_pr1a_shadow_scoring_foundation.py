"""pr1a_shadow_scoring_foundation

Revision ID: b7c8d9e0f1a2
Revises: d9e8f7a6b5c4
Create Date: 2026-06-04 12:00:00.000000

PR1a foundation-only schema:
- shadow scoring + evidence + score run audit tables
- provider-ready enrichment normalization tables
- contact/director schema scaffolding (no user-visible behavior yet)
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, Sequence[str], None] = "d9e8f7a6b5c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
        CREATE TABLE IF NOT EXISTS score_versions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            score_version TEXT NOT NULL,
            weights_version TEXT NOT NULL,
            rules_version TEXT NOT NULL,
            changed_by TEXT NOT NULL DEFAULT 'system',
            change_reason TEXT NOT NULL DEFAULT 'initial baseline',
            approved_by TEXT,
            effective_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (score_version, weights_version, rules_version)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS lead_signal_scores (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            snapshot_timestamp TIMESTAMPTZ NOT NULL,
            score_state TEXT NOT NULL CHECK (score_state IN ('unscored', 'scored', 'failed')),
            fit_score INTEGER NOT NULL CHECK (fit_score BETWEEN 0 AND 100),
            founder_quality_score INTEGER NOT NULL CHECK (founder_quality_score BETWEEN 0 AND 100),
            keyword_score INTEGER NOT NULL CHECK (keyword_score BETWEEN 0 AND 100),
            risk_score INTEGER NOT NULL CHECK (risk_score BETWEEN 0 AND 100),
            priority_score INTEGER NOT NULL CHECK (priority_score BETWEEN 0 AND 100),
            score_version TEXT NOT NULL,
            weights_version TEXT NOT NULL,
            rules_version TEXT NOT NULL,
            evidence_hash TEXT NOT NULL,
            why_output TEXT NOT NULL,
            computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            is_current BOOLEAN NOT NULL DEFAULT TRUE
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_lead_signal_scores_one_current
        ON lead_signal_scores(company_id)
        WHERE is_current = TRUE
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_lead_signal_scores_priority
        ON lead_signal_scores(priority_score DESC)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS lead_score_evidence (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            score_id UUID NOT NULL REFERENCES lead_signal_scores(id) ON DELETE CASCADE,
            company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            evidence_json JSONB NOT NULL,
            evidence_hash TEXT NOT NULL,
            why_output TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (score_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_lead_score_evidence_company
        ON lead_score_evidence(company_id, created_at DESC)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS score_runs (
            run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            lead_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            trigger_type TEXT NOT NULL CHECK (
                trigger_type IN ('nightly', 'manual', 'webhook', 'backfill', 'view')
            ),
            score_version TEXT NOT NULL,
            weights_version TEXT NOT NULL,
            rules_version TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('success', 'failure', 'skipped')),
            duration_ms INTEGER NOT NULL DEFAULT 0 CHECK (duration_ms >= 0),
            error_code TEXT,
            error_message TEXT,
            evidence_hash TEXT,
            snapshot_timestamp TIMESTAMPTZ,
            idempotency_key TEXT,
            source_event_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_score_runs_lead_created
        ON score_runs(lead_id, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_score_runs_idempotency
        ON score_runs(lead_id, trigger_type, idempotency_key)
        WHERE idempotency_key IS NOT NULL
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS company_enrichment (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            enrichment_tier TEXT NOT NULL CHECK (enrichment_tier IN ('free', 'paid', 'monitoring')),
            status TEXT NOT NULL DEFAULT 'success' CHECK (status IN ('success', 'failure', 'skipped')),
            source_confidence NUMERIC(5,2),
            source_payload JSONB NOT NULL,
            fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_company_enrichment_company_provider
        ON company_enrichment(company_id, provider, fetched_at DESC)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS normalized_company_metrics (
            company_id UUID PRIMARY KEY REFERENCES companies(id) ON DELETE CASCADE,
            website TEXT,
            company_linkedin_url TEXT,
            turnover_estimate NUMERIC(18,2),
            employees_estimate INTEGER,
            credit_band TEXT,
            failure_risk TEXT,
            group_structure JSONB,
            ownership_chain JSONB,
            source_freshness_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS company_contacts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            full_name TEXT,
            role_title TEXT,
            email TEXT,
            phone TEXT,
            linkedin_url TEXT,
            source TEXT NOT NULL,
            reachability_status TEXT NOT NULL DEFAULT 'discovered'
                CHECK (reachability_status IN ('discovered', 'validated', 'rejected_by_user', 'unverifiable')),
            confidence_score NUMERIC(5,2),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_company_contacts_company
        ON company_contacts(company_id, created_at DESC)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS decision_maker_contacts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            contact_id UUID REFERENCES company_contacts(id) ON DELETE SET NULL,
            full_name TEXT NOT NULL,
            role_title TEXT,
            linkedin_url TEXT,
            source TEXT NOT NULL,
            reachability_status TEXT NOT NULL DEFAULT 'discovered'
                CHECK (reachability_status IN ('discovered', 'validated', 'rejected_by_user', 'unverifiable')),
            confidence_score NUMERIC(5,2),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_decision_maker_contacts_company
        ON decision_maker_contacts(company_id, created_at DESC)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS director_profiles (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            full_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            appointed_on DATE,
            resigned_on DATE,
            active_appointments INTEGER,
            dissolution_ratio NUMERIC(6,3),
            sector_overlap_score INTEGER CHECK (sector_overlap_score BETWEEN 0 AND 100),
            source TEXT NOT NULL DEFAULT 'companies_house',
            raw_data JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (company_id, normalized_name, appointed_on)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_director_profiles_company
        ON director_profiles(company_id, created_at DESC)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS director_contact_candidates (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            director_profile_id UUID NOT NULL REFERENCES director_profiles(id) ON DELETE CASCADE,
            company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            candidate_url TEXT,
            candidate_handle TEXT,
            source TEXT NOT NULL,
            confidence_score NUMERIC(5,2),
            review_status TEXT NOT NULL DEFAULT 'pending'
                CHECK (review_status IN ('pending', 'confirmed', 'rejected')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_director_contact_candidates_profile
        ON director_contact_candidates(director_profile_id, created_at DESC)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS director_social_profiles (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            director_profile_id UUID NOT NULL REFERENCES director_profiles(id) ON DELETE CASCADE,
            company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            platform TEXT NOT NULL,
            profile_url TEXT NOT NULL,
            confidence_score NUMERIC(5,2),
            is_verified BOOLEAN NOT NULL DEFAULT FALSE,
            verified_by TEXT,
            verified_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (director_profile_id, platform, profile_url)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_director_social_profiles_profile
        ON director_social_profiles(director_profile_id, created_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_director_social_profiles_profile")
    op.execute("DROP TABLE IF EXISTS director_social_profiles")
    op.execute("DROP INDEX IF EXISTS idx_director_contact_candidates_profile")
    op.execute("DROP TABLE IF EXISTS director_contact_candidates")
    op.execute("DROP INDEX IF EXISTS idx_director_profiles_company")
    op.execute("DROP TABLE IF EXISTS director_profiles")
    op.execute("DROP INDEX IF EXISTS idx_decision_maker_contacts_company")
    op.execute("DROP TABLE IF EXISTS decision_maker_contacts")
    op.execute("DROP INDEX IF EXISTS idx_company_contacts_company")
    op.execute("DROP TABLE IF EXISTS company_contacts")
    op.execute("DROP TABLE IF EXISTS normalized_company_metrics")
    op.execute("DROP INDEX IF EXISTS idx_company_enrichment_company_provider")
    op.execute("DROP TABLE IF EXISTS company_enrichment")
    op.execute("DROP INDEX IF EXISTS idx_score_runs_idempotency")
    op.execute("DROP INDEX IF EXISTS idx_score_runs_lead_created")
    op.execute("DROP TABLE IF EXISTS score_runs")
    op.execute("DROP INDEX IF EXISTS idx_lead_score_evidence_company")
    op.execute("DROP TABLE IF EXISTS lead_score_evidence")
    op.execute("DROP INDEX IF EXISTS idx_lead_signal_scores_priority")
    op.execute("DROP INDEX IF EXISTS idx_lead_signal_scores_one_current")
    op.execute("DROP TABLE IF EXISTS lead_signal_scores")
    op.execute("DROP TABLE IF EXISTS score_versions")
    op.execute("ALTER TABLE companies DROP COLUMN IF EXISTS score_state_updated_at")
    op.execute("ALTER TABLE companies DROP COLUMN IF EXISTS score_state_reason")
    op.execute("ALTER TABLE companies DROP COLUMN IF EXISTS score_state")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'ck_companies_score_state'
            ) THEN
                ALTER TABLE companies DROP CONSTRAINT ck_companies_score_state;
            END IF;
        END $$;
        """
    )
