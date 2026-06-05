"""ensure canonical status constraints are applied

Revision ID: e7f8a9b0c1d2
Revises: f6a7b8c9d0e1
Create Date: 2026-06-05

Idempotent guard migration.

The c3d4e5f6a7b8 canonicalize migration used a pattern-based DROP to remove
old status check constraints. On some database states the inline CHECK
constraint from the initial schema (auto-named rm_actions_status_check) was
not caught by that pattern. This migration explicitly drops it by name and
ensures the canonical lowercase constraint is in place.

Safe to run multiple times — all operations are conditional.
"""

from alembic import op

revision = "e7f8a9b0c1d2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None

_CANONICAL = (
    "new", "reviewing", "qualified", "not_relevant",
    "deferred", "contacted", "onboarding", "not_fit",
)


def upgrade() -> None:
    # Drop old-style constraints by exact name (initial schema auto-generated names)
    op.execute(
        "ALTER TABLE rm_actions DROP CONSTRAINT IF EXISTS rm_actions_status_check"
    )
    op.execute(
        "ALTER TABLE introducer_actions DROP CONSTRAINT IF EXISTS introducer_actions_status_check"
    )

    # Drop any other pattern-matched status check constraints that may remain
    for table in ("rm_actions", "introducer_actions"):
        op.execute(
            f"""
            DO $$
            DECLARE c RECORD;
            BEGIN
                FOR c IN
                    SELECT conname FROM pg_constraint
                    WHERE conrelid = '{table}'::regclass
                      AND contype = 'c'
                      AND pg_get_constraintdef(oid) ILIKE '%status%'
                      AND conname NOT IN (
                        'ck_rm_actions_status_canonical',
                        'ck_introducer_actions_status_canonical'
                      )
                LOOP
                    EXECUTE format('ALTER TABLE {table} DROP CONSTRAINT IF EXISTS %I', c.conname);
                END LOOP;
            END $$;
            """
        )

    # Ensure canonical constraint exists (idempotent)
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_rm_actions_status_canonical'
                  AND conrelid = 'rm_actions'::regclass
            ) THEN
                ALTER TABLE rm_actions
                ADD CONSTRAINT ck_rm_actions_status_canonical
                CHECK (status IN {repr(_CANONICAL)});
            END IF;
        END $$;
        """
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_introducer_actions_status_canonical'
                  AND conrelid = 'introducer_actions'::regclass
            ) THEN
                ALTER TABLE introducer_actions
                ADD CONSTRAINT ck_introducer_actions_status_canonical
                CHECK (status IN {repr(_CANONICAL)});
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    pass  # Irreversible — do not restore old capitalized constraints
