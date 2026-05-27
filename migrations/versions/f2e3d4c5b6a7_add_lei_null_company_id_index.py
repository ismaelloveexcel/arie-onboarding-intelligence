"""add_lei_null_company_id_index

Revision ID: f2e3d4c5b6a7
Revises: c4cd676e9a1b
Create Date: 2026-05-27 12:00:00.000000

Adds a partial index on lei_records(id) WHERE company_id IS NULL to support
efficient batched lookups during the LEI backfill step.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'f2e3d4c5b6a7'
down_revision: Union[str, Sequence[str], None] = 'c4cd676e9a1b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_lei_null_company_id
        ON lei_records(id)
        WHERE company_id IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_lei_null_company_id")
