"""add_missing_performance_indexes

Revision ID: fae29d511bb1
Revises: d0ed8a831df0
Create Date: 2026-05-26 00:32:57.936211

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fae29d511bb1'
down_revision: Union[str, Sequence[str], None] = 'd0ed8a831df0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # companies: used in _refresh_queue WHERE c.canonical_company_id IS NULL
    # and for deduplication lookups
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_companies_canonical_company_id "
        "ON companies(canonical_company_id)"
    )
    # audit_log: lead_detail page queries entity_id + ORDER BY created_at DESC
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_log_entity_id_created_at "
        "ON audit_log(entity_id, created_at DESC)"
    )
    # queue_snapshot: filter by tier on the main queue page
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_queue_snapshot_tier "
        "ON queue_snapshot(tier)"
    )
    # queue_snapshot: filter by jurisdiction on the main queue page
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_queue_snapshot_jurisdiction "
        "ON queue_snapshot(jurisdiction)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_companies_canonical_company_id")
    op.execute("DROP INDEX IF EXISTS idx_audit_log_entity_id_created_at")
    op.execute("DROP INDEX IF EXISTS idx_queue_snapshot_tier")
    op.execute("DROP INDEX IF EXISTS idx_queue_snapshot_jurisdiction")
