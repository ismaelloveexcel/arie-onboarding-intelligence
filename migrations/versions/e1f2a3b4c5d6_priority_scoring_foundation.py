"""priority_scoring_foundation

Adds PR 1 of the lead engine roadmap:
- new dimensional scores on lead_scores (arie_fit, keyword, freshness, founder,
  cross_border, risk, priority)
- reachability_status, lead_readiness, enrichment_tier, why_reasons on lead_scores
- scoring_weights + lead_keywords config tables (seeded)
- empty scaffolding tables for company contacts, decision-maker contacts,
  director matching, and vendor enrichment

UI changes are intentionally minimal in this PR; later PRs depend on these
columns and tables existing.

Revision ID: e1f2a3b4c5d6
Revises: c0d1e2f3a4b5
Create Date: 2026-06-04
"""

from typing import Sequence, Union

from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "c0d1e2f3a4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- lead_scores: new dimensional + status columns ---
    op.execute("""
        ALTER TABLE lead_scores
            ADD COLUMN IF NOT EXISTS arie_fit_score        INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS keyword_score         INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS freshness_score       INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS founder_quality_score INTEGER NOT NULL DEFAULT 50,
            ADD COLUMN IF NOT EXISTS cross_border_score    INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS risk_score            INTEGER NOT NULL DEFAULT 50,
            ADD COLUMN IF NOT EXISTS priority_score        INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS reachability_status   TEXT    NOT NULL DEFAULT 'research_required',
            ADD COLUMN IF NOT EXISTS lead_readiness        TEXT    NOT NULL DEFAULT 'discovered',
            ADD COLUMN IF NOT EXISTS enrichment_tier       TEXT    NOT NULL DEFAULT 'C',
            ADD COLUMN IF NOT EXISTS why_reasons           JSONB   NOT NULL DEFAULT '[]'::jsonb
    """)

    # CHECK constraints (added separately so re-runs are safe)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'lead_scores_reachability_status_chk'
            ) THEN
                ALTER TABLE lead_scores
                    ADD CONSTRAINT lead_scores_reachability_status_chk
                    CHECK (reachability_status IN ('ready_outreach','research_required','no_contact_path'));
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'lead_scores_lead_readiness_chk'
            ) THEN
                ALTER TABLE lead_scores
                    ADD CONSTRAINT lead_scores_lead_readiness_chk
                    CHECK (lead_readiness IN ('discovered','qualified','contactable','engaged'));
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'lead_scores_enrichment_tier_chk'
            ) THEN
                ALTER TABLE lead_scores
                    ADD CONSTRAINT lead_scores_enrichment_tier_chk
                    CHECK (enrichment_tier IN ('A','B','C'));
            END IF;
        END$$;
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_lead_scores_priority
        ON lead_scores(priority_score DESC)
        WHERE is_current = TRUE
    """)

    # --- scoring_weights: editable priority formula ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS scoring_weights (
            id            TEXT PRIMARY KEY,
            weights       JSONB NOT NULL,
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_by    TEXT
        )
    """)
    op.execute(r"""
        INSERT INTO scoring_weights (id, weights)
        VALUES ('priority', '{"arie_fit"\:0.40,"founder"\:0.30,"lead"\:0.20,"risk"\:0.10}'::jsonb)
        ON CONFLICT (id) DO NOTHING
    """)

    # --- lead_keywords: configurable name-keyword signals ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS lead_keywords (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            term        TEXT NOT NULL,
            polarity    TEXT NOT NULL CHECK (polarity IN ('positive','negative')),
            weight      INTEGER NOT NULL DEFAULT 10,
            active      BOOLEAN NOT NULL DEFAULT TRUE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(term)
        )
    """)
    # Seed defaults — RM team can edit later via DB.
    seed_keywords = [
        # positive — cross-border, financial, holding signals
        ("capital", "positive", 15),
        ("holdings", "positive", 15),
        ("ventures", "positive", 10),
        ("partners", "positive", 10),
        ("international", "positive", 10),
        ("global", "positive", 10),
        ("group", "positive", 8),
        ("trading", "positive", 10),
        ("import", "positive", 12),
        ("export", "positive", 12),
        ("logistics", "positive", 10),
        ("commodities", "positive", 12),
        ("fund", "positive", 15),
        ("wealth", "positive", 12),
        ("asset", "positive", 10),
        ("invest", "positive", 10),
        # negative — out-of-ICP signals
        ("charity", "negative", 30),
        ("foundation", "negative", 20),
        ("church", "negative", 30),
        ("school", "negative", 25),
        ("club", "negative", 20),
        ("dormant", "negative", 40),
    ]
    for term, polarity, weight in seed_keywords:
        op.execute(f"""
            INSERT INTO lead_keywords (term, polarity, weight)
            VALUES ('{term}', '{polarity}', {weight})
            ON CONFLICT (term) DO NOTHING
            """)

    # --- contact assets (empty in PR 1, populated in PR 2) ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS company_contacts (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            website         TEXT,
            generic_email   TEXT,
            linkedin_url    TEXT,
            phone           TEXT,
            source          TEXT,
            confidence      NUMERIC(4,3),
            verified_at     TIMESTAMPTZ,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(company_id)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS decision_maker_contacts (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id          UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            director_profile_id UUID,
            officer_id          UUID,
            full_name           TEXT NOT NULL,
            role                TEXT,
            email               TEXT,
            phone               TEXT,
            linkedin_url        TEXT,
            confidence          NUMERIC(4,3),
            source              TEXT,
            verified_at         TIMESTAMPTZ,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_dm_contacts_company ON decision_maker_contacts(company_id)"
    )

    # --- director matching scaffolding (empty in PR 1, used by PR 3) ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS director_profiles (
            id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            officer_id                UUID,
            full_name                 TEXT NOT NULL,
            normalized_name           TEXT NOT NULL,
            jurisdictions             JSONB NOT NULL DEFAULT '[]'::jsonb,
            active_appointments_count INTEGER NOT NULL DEFAULT 0,
            source                    TEXT,
            created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_director_profiles_norm ON director_profiles(normalized_name)"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS director_contact_candidates (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            director_profile_id UUID NOT NULL REFERENCES director_profiles(id) ON DELETE CASCADE,
            email               TEXT,
            phone               TEXT,
            source              TEXT,
            confidence          NUMERIC(4,3),
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS director_social_profiles (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            director_profile_id UUID NOT NULL REFERENCES director_profiles(id) ON DELETE CASCADE,
            platform            TEXT NOT NULL,
            url                 TEXT NOT NULL,
            confidence          NUMERIC(4,3),
            last_verified       TIMESTAMPTZ,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # --- vendor enrichment scaffolding (empty in PR 1, used by PR 5/6/8) ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS company_enrichment (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id  UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            source      TEXT NOT NULL,
            payload     JSONB NOT NULL,
            fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at  TIMESTAMPTZ
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_company_enrichment_company ON company_enrichment(company_id, source)"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS normalized_company_metrics (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            employees       INTEGER,
            revenue         NUMERIC(18,2),
            last_filing     DATE,
            credit_rating   TEXT,
            source          TEXT,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(company_id)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS normalized_company_metrics")
    op.execute("DROP TABLE IF EXISTS company_enrichment")
    op.execute("DROP TABLE IF EXISTS director_social_profiles")
    op.execute("DROP TABLE IF EXISTS director_contact_candidates")
    op.execute("DROP TABLE IF EXISTS director_profiles")
    op.execute("DROP TABLE IF EXISTS decision_maker_contacts")
    op.execute("DROP TABLE IF EXISTS company_contacts")
    op.execute("DROP TABLE IF EXISTS lead_keywords")
    op.execute("DROP TABLE IF EXISTS scoring_weights")
    op.execute("DROP INDEX IF EXISTS idx_lead_scores_priority")
    op.execute("""
        ALTER TABLE lead_scores
            DROP CONSTRAINT IF EXISTS lead_scores_reachability_status_chk,
            DROP CONSTRAINT IF EXISTS lead_scores_lead_readiness_chk,
            DROP CONSTRAINT IF EXISTS lead_scores_enrichment_tier_chk,
            DROP COLUMN IF EXISTS why_reasons,
            DROP COLUMN IF EXISTS enrichment_tier,
            DROP COLUMN IF EXISTS lead_readiness,
            DROP COLUMN IF EXISTS reachability_status,
            DROP COLUMN IF EXISTS priority_score,
            DROP COLUMN IF EXISTS risk_score,
            DROP COLUMN IF EXISTS cross_border_score,
            DROP COLUMN IF EXISTS founder_quality_score,
            DROP COLUMN IF EXISTS freshness_score,
            DROP COLUMN IF EXISTS keyword_score,
            DROP COLUMN IF EXISTS arie_fit_score
    """)
