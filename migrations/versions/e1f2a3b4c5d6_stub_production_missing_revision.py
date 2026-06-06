"""stub: reconcile production database missing revision

Revision ID: e1f2a3b4c5d6
Revises: e5f6a7b8c9d0
Create Date: 2026-06-06

No-op stub for a revision applied to the production database that was never
committed to the repository. Sits on a parallel branch alongside e6f7a8b9c0d1
(the test DB stub). Both are merged by d5e6f7a8b9c0.
"""

revision = "e1f2a3b4c5d6"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
