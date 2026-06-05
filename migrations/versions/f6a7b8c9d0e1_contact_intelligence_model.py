"""contact intelligence data model

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-06-05

Extends lead_contacts with enrichment-readiness fields and adds
company_web_intelligence for web/social presence data.
No external API dependency — schema is provider-agnostic.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Extend lead_contacts with enrichment-readiness fields
    op.add_column("lead_contacts", sa.Column("email_confidence", sa.Text()))
    op.add_column("lead_contacts", sa.Column("email_status", sa.Text()))
    op.add_column("lead_contacts", sa.Column("linkedin_verified", sa.Boolean(), server_default="false"))
    op.add_column("lead_contacts", sa.Column("nationality", sa.Text()))
    op.add_column("lead_contacts", sa.Column("country_of_residence", sa.Text()))
    op.add_column("lead_contacts", sa.Column("enrichment_source", sa.Text()))
    op.add_column("lead_contacts", sa.Column("enrichment_status", sa.Text(), server_default="'pending'"))
    op.add_column("lead_contacts", sa.Column("enrichment_attempted_at", sa.DateTime(timezone=True)))
    op.add_column("lead_contacts", sa.Column("enrichment_completed_at", sa.DateTime(timezone=True)))
    op.add_column("lead_contacts", sa.Column("raw_enrichment_data", JSONB()))
    op.add_column("lead_contacts", sa.Column("is_decision_maker", sa.Boolean(), server_default="false"))
    op.add_column("lead_contacts", sa.Column("contact_priority", sa.Integer(), server_default="0"))

    # Web intelligence table — provider-agnostic, enrichment-ready
    op.create_table(
        "company_web_intelligence",
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
            unique=True,
        ),
        sa.Column("website_status", sa.Text()),          # active / parked / unreachable / unknown
        sa.Column("website_title", sa.Text()),
        sa.Column("website_description", sa.Text()),
        sa.Column("linkedin_company_url", sa.Text()),
        sa.Column("linkedin_employee_count", sa.Integer()),
        sa.Column("linkedin_industry", sa.Text()),
        sa.Column("twitter_handle", sa.Text()),
        sa.Column("crunchbase_url", sa.Text()),
        sa.Column("estimated_founded_year", sa.Integer()),
        sa.Column("hq_country", sa.Text()),
        sa.Column("tech_stack", JSONB()),                # future: BuiltWith / Wappalyzer
        sa.Column("news_mentions_30d", sa.Integer()),
        sa.Column("enrichment_source", sa.Text()),       # which provider filled this
        sa.Column("enrichment_status", sa.Text(), server_default="'pending'"),
        sa.Column("enriched_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_company_web_intelligence_company_id",
        "company_web_intelligence",
        ["company_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_company_web_intelligence_company_id", "company_web_intelligence")
    op.drop_table("company_web_intelligence")

    for col in [
        "email_confidence", "email_status", "linkedin_verified",
        "nationality", "country_of_residence",
        "enrichment_source", "enrichment_status",
        "enrichment_attempted_at", "enrichment_completed_at",
        "raw_enrichment_data", "is_decision_maker", "contact_priority",
    ]:
        op.drop_column("lead_contacts", col)
