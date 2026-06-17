"""add companies.enrichment_attempts to support transient-vs-permanent retry

Previously CH enrichment always stamped last_enriched_at even on transient
failures (timeouts, 5xx, rate limits), so those companies were never retried
and dashboard coverage overstated success. This column lets the batch retry
transient failures a bounded number of times while still avoiding infinite
retries.

Revision ID: f5c3d4e5f6a7
Revises: f4b2c3d4e5f6
Create Date: 2026-06-17
"""

from alembic import op

revision = "f5c3d4e5f6a7"
down_revision = "f4b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE companies "
        "ADD COLUMN IF NOT EXISTS enrichment_attempts INTEGER NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE companies DROP COLUMN IF EXISTS enrichment_attempts")
