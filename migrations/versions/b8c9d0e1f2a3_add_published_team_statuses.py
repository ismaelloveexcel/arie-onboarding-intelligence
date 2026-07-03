"""add published team workflow statuses

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-07-03
"""

from alembic import op


revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


_OLD_CANONICAL = (
    "new", "reviewing", "qualified", "not_relevant",
    "deferred", "contacted", "onboarding", "not_fit",
)

_NEW_CANONICAL = (
    "new", "reviewing", "qualified", "not_relevant",
    "deferred", "contacted", "sent_to_team", "in_progress",
    "follow_up", "onboarding", "not_fit",
)


def _drop_legacy_status_constraints(table: str, canonical_constraint: str) -> None:
    for constraint in (
        canonical_constraint,
        f"{table}_status_check",
        f"{table}_status_chk",
    ):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}")


def _canonicalize_existing_statuses(table: str) -> None:
    op.execute(
        f"""
        UPDATE {table}
        SET status = CASE
            WHEN lower(replace(trim(status), '  ', ' '))
                IN ('new', '') THEN 'new'
            WHEN lower(replace(trim(status), '  ', ' '))
                IN ('researching', 'reviewing', 'in review') THEN 'reviewing'
            WHEN lower(replace(trim(status), '  ', ' '))
                IN ('qualified', 'outreach ready') THEN 'qualified'
            WHEN lower(replace(trim(status), '  ', ' '))
                IN ('not relevant', 'not_relevant') THEN 'not_relevant'
            WHEN lower(replace(trim(status), '  ', ' '))
                IN ('deferred', 'later') THEN 'deferred'
            WHEN lower(replace(trim(status), '  ', ' '))
                IN ('contacted') THEN 'contacted'
            WHEN lower(replace(trim(status), '  ', ' '))
                IN ('sent to team', 'sent_to_team', 'published') THEN 'sent_to_team'
            WHEN lower(replace(trim(status), '  ', ' '))
                IN ('in progress', 'in_progress', 'working') THEN 'in_progress'
            WHEN lower(replace(trim(status), '  ', ' '))
                IN ('follow up', 'follow-up', 'follow_up', 'followup') THEN 'follow_up'
            WHEN lower(replace(trim(status), '  ', ' '))
                IN ('onboarding') THEN 'onboarding'
            WHEN lower(replace(trim(status), '  ', ' '))
                IN ('not fit', 'not_fit', 'closed - not fit', 'closed-not fit') THEN 'not_fit'
            ELSE status
        END
        WHERE status IS NOT NULL
        """
    )


def _replace_constraint(table: str, constraint: str, statuses: tuple[str, ...]) -> None:
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}")
    op.execute(
        f"""
        ALTER TABLE {table}
        ADD CONSTRAINT {constraint}
        CHECK (status IN {repr(statuses)})
        """
    )


def upgrade() -> None:
    _drop_legacy_status_constraints(
        "rm_actions",
        "ck_rm_actions_status_canonical",
    )
    _drop_legacy_status_constraints(
        "introducer_actions",
        "ck_introducer_actions_status_canonical",
    )
    _canonicalize_existing_statuses("rm_actions")
    _canonicalize_existing_statuses("introducer_actions")
    _replace_constraint(
        "rm_actions",
        "ck_rm_actions_status_canonical",
        _NEW_CANONICAL,
    )
    _replace_constraint(
        "introducer_actions",
        "ck_introducer_actions_status_canonical",
        _NEW_CANONICAL,
    )


def downgrade() -> None:
    _replace_constraint(
        "rm_actions",
        "ck_rm_actions_status_canonical",
        _OLD_CANONICAL,
    )
    _replace_constraint(
        "introducer_actions",
        "ck_introducer_actions_status_canonical",
        _OLD_CANONICAL,
    )
