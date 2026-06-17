"""align rm_actions.status CHECK constraint with the canonical 8-status UI workflow

The application UI (src/main.py _STATUSES) offers 8 statuses, but the original
rm_actions CHECK constraint only permitted the legacy set. Picking any of the
new statuses (e.g. "Outreach Ready", "Opportunity", "Client") raised a
constraint violation and the RM save failed. This migration makes the DB match
the canonical UI workflow and migrates any legacy values forward first.

Revision ID: f3a1b2c3d4e5
Revises: e1f2a3b4c5d6
Create Date: 2026-06-17
"""

from alembic import op

revision = "f3a1b2c3d4e5"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None

# Canonical statuses — must stay in sync with src/main.py _STATUSES.
_CANONICAL = (
    "New",
    "Researching",
    "Qualified",
    "Outreach Ready",
    "Contacted",
    "Opportunity",
    "Client",
    "Closed — Not Fit",  # em dash, matches the Python string exactly
)

# Legacy value -> canonical value (nearest equivalent).
_FORWARD_MAP = {
    "Reviewing": "Researching",
    "Deferred": "Researching",
    "Not Relevant": "Closed — Not Fit",
    "Not Fit": "Closed — Not Fit",
    "Onboarding": "Opportunity",
}


def upgrade() -> None:
    # 1) Migrate any existing legacy values so the new constraint will validate.
    for old, new in _FORWARD_MAP.items():
        op.execute(
            f"UPDATE rm_actions SET status = '{new}' WHERE status = '{old}'"
        )

    # 2) Drop the old inline CHECK (Postgres auto-names it <table>_<col>_check)
    #    and any prior explicitly-named variant, then add the canonical one.
    op.execute("ALTER TABLE rm_actions DROP CONSTRAINT IF EXISTS rm_actions_status_check")
    op.execute("ALTER TABLE rm_actions DROP CONSTRAINT IF EXISTS rm_actions_status_chk")

    allowed = ",".join(f"'{s}'" for s in _CANONICAL)
    op.execute(
        f"ALTER TABLE rm_actions ADD CONSTRAINT rm_actions_status_chk "
        f"CHECK (status IN ({allowed}))"
    )


def downgrade() -> None:
    # Revert to the original legacy constraint. Map forward-only values back to
    # the closest legacy equivalent so the old constraint validates.
    op.execute("UPDATE rm_actions SET status = 'Reviewing' WHERE status = 'Researching'")
    op.execute("UPDATE rm_actions SET status = 'Onboarding' WHERE status = 'Opportunity'")
    op.execute("UPDATE rm_actions SET status = 'Onboarding' WHERE status = 'Client'")
    op.execute("UPDATE rm_actions SET status = 'Contacted' WHERE status = 'Outreach Ready'")
    op.execute("UPDATE rm_actions SET status = 'Not Fit' WHERE status = 'Closed — Not Fit'")

    op.execute("ALTER TABLE rm_actions DROP CONSTRAINT IF EXISTS rm_actions_status_chk")
    op.execute(
        "ALTER TABLE rm_actions ADD CONSTRAINT rm_actions_status_check "
        "CHECK (status IN ('New','Reviewing','Qualified','Not Relevant',"
        "'Deferred','Contacted','Onboarding','Not Fit'))"
    )
