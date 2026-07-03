"""Add RM feedback fields and route provenance columns.

Additive only — extends existing tables, no destructive changes:
- rm_actions gains next_action, next_action_due_date, feedback, feedback_note
  (preserves the existing one-row-per-company model; status/notes/follow_up
  logic is untouched).
- route_recommendations gains discrete provenance columns so a route's source,
  entry method, and freshness are first-class rather than free text in
  evidence_summary.

Revision ID: f3a4b5c6d7e8
Revises: d1e2f3a4b5c6
Create Date: 2026-06-22
"""

from alembic import op


revision = "f3a4b5c6d7e8"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- RM workflow feedback (on the existing rm_actions row) ---------------
    op.execute("ALTER TABLE rm_actions ADD COLUMN IF NOT EXISTS next_action TEXT")
    op.execute(
        "ALTER TABLE rm_actions ADD COLUMN IF NOT EXISTS next_action_due_date DATE"
    )
    op.execute("ALTER TABLE rm_actions ADD COLUMN IF NOT EXISTS feedback TEXT")
    op.execute("ALTER TABLE rm_actions ADD COLUMN IF NOT EXISTS feedback_note TEXT")
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'rm_actions_feedback_chk'
            ) THEN
                ALTER TABLE rm_actions
                ADD CONSTRAINT rm_actions_feedback_chk CHECK (
                    feedback IS NULL OR feedback IN (
                        'useful', 'wrong_contact', 'not_relevant', 'duplicate',
                        'contacted', 'meeting_booked', 'won', 'lost'
                    )
                );
            END IF;
        END $$;
    """)

    # --- Route provenance (on route_recommendations) ------------------------
    op.execute(
        "ALTER TABLE route_recommendations "
        "ADD COLUMN IF NOT EXISTS secondary_contact_route TEXT"
    )
    op.execute(
        "ALTER TABLE route_recommendations "
        "ADD COLUMN IF NOT EXISTS route_source_url TEXT"
    )
    op.execute(
        "ALTER TABLE route_recommendations "
        "ADD COLUMN IF NOT EXISTS route_source_label TEXT"
    )
    op.execute(
        "ALTER TABLE route_recommendations "
        "ADD COLUMN IF NOT EXISTS route_source_type TEXT"
    )
    op.execute(
        "ALTER TABLE route_recommendations "
        "ADD COLUMN IF NOT EXISTS route_last_checked_at TIMESTAMPTZ"
    )
    op.execute(
        "ALTER TABLE route_recommendations "
        "ADD COLUMN IF NOT EXISTS route_entry_method TEXT "
        "NOT NULL DEFAULT 'system_detected'"
    )
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'route_recommendations_entry_method_chk'
            ) THEN
                ALTER TABLE route_recommendations
                ADD CONSTRAINT route_recommendations_entry_method_chk CHECK (
                    route_entry_method IN ('system_detected', 'manual', 'import')
                );
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE route_recommendations "
        "DROP CONSTRAINT IF EXISTS route_recommendations_entry_method_chk"
    )
    for column in (
        "route_entry_method",
        "route_last_checked_at",
        "route_source_type",
        "route_source_label",
        "route_source_url",
        "secondary_contact_route",
    ):
        op.execute(
            f"ALTER TABLE route_recommendations DROP COLUMN IF EXISTS {column}"
        )

    op.execute(
        "ALTER TABLE rm_actions DROP CONSTRAINT IF EXISTS rm_actions_feedback_chk"
    )
    for column in ("feedback_note", "feedback", "next_action_due_date", "next_action"):
        op.execute(f"ALTER TABLE rm_actions DROP COLUMN IF EXISTS {column}")
