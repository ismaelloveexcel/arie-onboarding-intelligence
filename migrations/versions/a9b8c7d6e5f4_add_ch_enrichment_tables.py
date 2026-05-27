"""add_ch_enrichment_tables

Revision ID: a9b8c7d6e5f4
Revises: f2e3d4c5b6a7
Create Date: 2026-05-27 14:00:00.000000

Adds company_officers and company_pscs tables for Companies House PSC/officer
enrichment, and adds last_enriched_at column to companies for scheduling gating.
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'a9b8c7d6e5f4'
down_revision: Union[str, Sequence[str], None] = 'f2e3d4c5b6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS company_officers (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id           UUID NOT NULL REFERENCES companies(id),
            officer_name         TEXT NOT NULL,
            role                 TEXT NOT NULL,
            appointed_on         DATE,
            resigned_on          DATE,
            nationality          TEXT,
            country_of_residence TEXT,
            date_of_birth_year   INT,
            date_of_birth_month  INT,
            raw_json             JSONB NOT NULL,
            fetched_at           TIMESTAMP DEFAULT NOW(),
            UNIQUE (company_id, officer_name, appointed_on)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_company_officers_company_id
        ON company_officers(company_id)
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS company_pscs (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id           UUID NOT NULL REFERENCES companies(id),
            name                 TEXT NOT NULL,
            kind                 TEXT NOT NULL,
            nationality          TEXT,
            country_of_residence TEXT,
            natures_of_control   TEXT[],
            notified_on          DATE,
            ceased_on            DATE,
            raw_json             JSONB NOT NULL,
            fetched_at           TIMESTAMP DEFAULT NOW(),
            UNIQUE (company_id, name, notified_on)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_company_pscs_company_id
        ON company_pscs(company_id)
    """)
    op.execute("""
        ALTER TABLE companies ADD COLUMN IF NOT EXISTS last_enriched_at TIMESTAMP
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE companies DROP COLUMN IF EXISTS last_enriched_at")
    op.execute("DROP TABLE IF EXISTS company_pscs")
    op.execute("DROP TABLE IF EXISTS company_officers")
