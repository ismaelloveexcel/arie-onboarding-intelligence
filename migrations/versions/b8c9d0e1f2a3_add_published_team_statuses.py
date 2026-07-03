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
