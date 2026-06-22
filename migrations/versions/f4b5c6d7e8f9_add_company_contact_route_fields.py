"""Ensure company_contacts carries company-level route fields.

The route operator and audit already read website / generic_email /
contact_form_url / confidence / verified_at / checked_by from company_contacts,
but this branch's base migration only created the per-person contact columns.
This additive, idempotent migration brings the branch schema in line with the
deployed schema (every column is IF NOT EXISTS, so it is a no-op where they
already exist) and gives the manual contact-route importer a home, with
provenance fields.

Revision ID: f4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-06-22
"""

from alembic import op


revision = "f4b5c6d7e8f9"
down_revision = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None


_COLUMNS = (
    "website TEXT",
    "generic_email TEXT",
    "contact_form_url TEXT",
    "confidence NUMERIC(5,2)",
    "verified_at TIMESTAMPTZ",
    "checked_by TEXT",
    "route_source_url TEXT",
    "route_source_label TEXT",
    "route_source_type TEXT",
    "route_entry_method TEXT",
)


def upgrade() -> None:
    for column in _COLUMNS:
        op.execute(f"ALTER TABLE company_contacts ADD COLUMN IF NOT EXISTS {column}")
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'company_contacts_route_entry_method_chk'
            ) THEN
                ALTER TABLE company_contacts
                ADD CONSTRAINT company_contacts_route_entry_method_chk CHECK (
                    route_entry_method IS NULL
                    OR route_entry_method IN ('system_detected', 'manual', 'import')
                );
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE company_contacts "
        "DROP CONSTRAINT IF EXISTS company_contacts_route_entry_method_chk"
    )
    for column in _COLUMNS:
        name = column.split()[0]
        op.execute(f"ALTER TABLE company_contacts DROP COLUMN IF EXISTS {name}")
