"""Add provenance and normalized introducer fields for route intelligence.

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-06-21
"""

from alembic import op


revision = "e2f3a4b5c6d7"
down_revision = "b1c2d3e4f5a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE companies ADD COLUMN registered_office_source TEXT")
    op.execute(
        "ALTER TABLE companies ADD COLUMN registered_office_checked_at TIMESTAMPTZ"
    )
    op.execute("""
        ALTER TABLE companies
        ADD COLUMN registered_office_confidence TEXT
        CHECK (registered_office_confidence IN ('high', 'medium', 'low'))
    """)
    op.execute("""
        ALTER TABLE companies
        ADD COLUMN registered_office_retrieval_status TEXT NOT NULL DEFAULT 'not_checked'
        CHECK (registered_office_retrieval_status IN (
            'not_checked', 'found', 'not_found', 'error', 'manual_review'
        ))
    """)

    op.execute("ALTER TABLE introducers ADD COLUMN email_domain TEXT")
    op.execute("ALTER TABLE introducers ADD COLUMN website TEXT")
    op.execute("ALTER TABLE introducers ADD COLUMN website_domain TEXT")
    op.execute("ALTER TABLE introducers ADD COLUMN normalized_address TEXT")
    op.execute("""
        ALTER TABLE introducers
        ADD COLUMN introducer_type TEXT NOT NULL DEFAULT 'unknown'
        CHECK (introducer_type IN (
            'management_company', 'csp', 'fiduciary', 'fund_administrator',
            'law_firm', 'accounting_firm', 'bank', 'consultant', 'other', 'unknown'
        ))
    """)
    op.execute("ALTER TABLE introducers ADD COLUMN category_source TEXT")
    op.execute("""
        ALTER TABLE introducers
        ADD COLUMN category_confidence TEXT
        CHECK (category_confidence IN ('high', 'medium', 'low'))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE introducers DROP COLUMN IF EXISTS category_confidence")
    op.execute("ALTER TABLE introducers DROP COLUMN IF EXISTS category_source")
    op.execute("ALTER TABLE introducers DROP COLUMN IF EXISTS introducer_type")
    op.execute("ALTER TABLE introducers DROP COLUMN IF EXISTS normalized_address")
    op.execute("ALTER TABLE introducers DROP COLUMN IF EXISTS website_domain")
    op.execute("ALTER TABLE introducers DROP COLUMN IF EXISTS website")
    op.execute("ALTER TABLE introducers DROP COLUMN IF EXISTS email_domain")
    op.execute(
        "ALTER TABLE companies DROP COLUMN IF EXISTS registered_office_retrieval_status"
    )
    op.execute(
        "ALTER TABLE companies DROP COLUMN IF EXISTS registered_office_confidence"
    )
    op.execute(
        "ALTER TABLE companies DROP COLUMN IF EXISTS registered_office_checked_at"
    )
    op.execute("ALTER TABLE companies DROP COLUMN IF EXISTS registered_office_source")
