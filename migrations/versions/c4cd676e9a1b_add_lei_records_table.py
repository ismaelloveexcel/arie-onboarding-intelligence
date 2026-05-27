"""add_lei_records_table

Revision ID: c4cd676e9a1b
Revises: b1c2d3e4f5a7
Create Date: 2026-05-27 10:32:36.199526

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c4cd676e9a1b'
down_revision: Union[str, Sequence[str], None] = 'b1c2d3e4f5a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS lei_records (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id UUID REFERENCES companies(id),
            lei_code TEXT NOT NULL,
            legal_name TEXT,
            jurisdiction TEXT,
            entity_status TEXT,
            registration_status TEXT,
            registered_on DATE,
            last_updated_on DATE,
            managing_lou TEXT,
            registered_as TEXT,
            gleif_url TEXT,
            raw_data JSONB,
            first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(lei_code)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_lei_records_company_id
        ON lei_records(company_id)
        WHERE company_id IS NOT NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_lei_records_registered_on
        ON lei_records(registered_on DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_lei_records_registered_as
        ON lei_records(registered_as)
        WHERE registered_as IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS lei_records")
