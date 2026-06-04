"""canonicalize_status_values

Revision ID: c3d4e5f6a7b8
Revises: b7c8d9e0f1a2
Create Date: 2026-06-04 15:35:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CANONICAL = ("new", "reviewing", "qualified", "not_relevant", "deferred", "contacted", "onboarding", "not_fit")


def _drop_status_check_constraints(table_name: str) -> None:
    op.execute(
        f"""
        DO $$
        DECLARE
            c RECORD;
        BEGIN
            FOR c IN
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = '{table_name}'::regclass
                  AND contype = 'c'
                  AND pg_get_constraintdef(oid) ILIKE '%status%'
            LOOP
                EXECUTE format('ALTER TABLE {table_name} DROP CONSTRAINT IF EXISTS %I', c.conname);
            END LOOP;
        END $$;
        """
    )


def upgrade() -> None:
    op.execute(
        """
        UPDATE rm_actions
        SET status = CASE
            WHEN lower(replace(replace(trim(status), '—', '-'), '  ', ' ')) IN ('new') THEN 'new'
            WHEN lower(replace(replace(trim(status), '—', '-'), '  ', ' ')) IN ('researching', 'reviewing') THEN 'reviewing'
            WHEN lower(replace(replace(trim(status), '—', '-'), '  ', ' ')) IN ('qualified', 'outreach ready') THEN 'qualified'
            WHEN lower(replace(replace(trim(status), '—', '-'), '  ', ' ')) = 'not relevant' THEN 'not_relevant'
            WHEN lower(replace(replace(trim(status), '—', '-'), '  ', ' ')) = 'deferred' THEN 'deferred'
            WHEN lower(replace(replace(trim(status), '—', '-'), '  ', ' ')) = 'contacted' THEN 'contacted'
            WHEN lower(replace(replace(trim(status), '—', '-'), '  ', ' ')) IN ('opportunity', 'client', 'onboarding') THEN 'onboarding'
            WHEN lower(replace(replace(trim(status), '—', '-'), '  ', ' ')) IN ('not fit', 'closed - not fit', 'closed-not fit', 'closed - not-fit') THEN 'not_fit'
            ELSE 'new'
        END
        """
    )
    op.execute(
        """
        UPDATE introducer_actions
        SET status = CASE
            WHEN lower(replace(replace(trim(status), '—', '-'), '  ', ' ')) IN ('new') THEN 'new'
            WHEN lower(replace(replace(trim(status), '—', '-'), '  ', ' ')) IN ('researching', 'reviewing') THEN 'reviewing'
            WHEN lower(replace(replace(trim(status), '—', '-'), '  ', ' ')) IN ('qualified', 'outreach ready') THEN 'qualified'
            WHEN lower(replace(replace(trim(status), '—', '-'), '  ', ' ')) = 'not relevant' THEN 'not_relevant'
            WHEN lower(replace(replace(trim(status), '—', '-'), '  ', ' ')) = 'deferred' THEN 'deferred'
            WHEN lower(replace(replace(trim(status), '—', '-'), '  ', ' ')) = 'contacted' THEN 'contacted'
            WHEN lower(replace(replace(trim(status), '—', '-'), '  ', ' ')) IN ('opportunity', 'client', 'onboarding') THEN 'onboarding'
            WHEN lower(replace(replace(trim(status), '—', '-'), '  ', ' ')) IN ('not fit', 'closed - not fit', 'closed-not fit', 'closed - not-fit') THEN 'not_fit'
            ELSE 'new'
        END
        """
    )

    _drop_status_check_constraints("rm_actions")
    _drop_status_check_constraints("introducer_actions")

    op.execute("ALTER TABLE rm_actions ALTER COLUMN status SET DEFAULT 'new'")
    op.execute("ALTER TABLE introducer_actions ALTER COLUMN status SET DEFAULT 'new'")
    op.execute(
        f"""
        ALTER TABLE rm_actions
        ADD CONSTRAINT ck_rm_actions_status_canonical
        CHECK (status IN {repr(_CANONICAL)})
        """
    )
    op.execute(
        f"""
        ALTER TABLE introducer_actions
        ADD CONSTRAINT ck_introducer_actions_status_canonical
        CHECK (status IN {repr(_CANONICAL)})
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE rm_actions DROP CONSTRAINT IF EXISTS ck_rm_actions_status_canonical")
    op.execute(
        "ALTER TABLE introducer_actions DROP CONSTRAINT IF EXISTS ck_introducer_actions_status_canonical"
    )
    op.execute("ALTER TABLE rm_actions ALTER COLUMN status SET DEFAULT 'New'")
    op.execute("ALTER TABLE introducer_actions ALTER COLUMN status SET DEFAULT 'New'")
    op.execute(
        """
        ALTER TABLE rm_actions
        ADD CONSTRAINT rm_actions_status_check
        CHECK (status IN ('New','Reviewing','Qualified','Not Relevant','Deferred','Contacted','Onboarding','Not Fit'))
        """
    )
    op.execute(
        """
        ALTER TABLE introducer_actions
        ADD CONSTRAINT introducer_actions_status_check
        CHECK (status IN ('New','Reviewing','Qualified','Not Relevant','Deferred','Contacted','Onboarding','Not Fit'))
        """
    )
