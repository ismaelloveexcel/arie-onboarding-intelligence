"""add lead_contacts

Revision ID: c1d2e3f4a5b6
Revises: fae29d511bb1
Create Date: 2026-06-01

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "c1d2e3f4a5b6"
down_revision = "fae29d511bb1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lead_contacts",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("role", sa.Text()),
        sa.Column("email", sa.Text()),
        sa.Column("phone", sa.Text()),
        sa.Column("linkedin_url", sa.Text()),
        sa.Column("source", sa.Text()),  # e.g. "companies_house", "manual"
        sa.Column("notes", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
    )
    op.create_index("ix_lead_contacts_company_id", "lead_contacts", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_lead_contacts_company_id", "lead_contacts")
    op.drop_table("lead_contacts")
