"""merge: reconcile divergent test and production database branches

Revision ID: d5e6f7a8b9c0
Revises: e6f7a8b9c0d1, e1f2a3b4c5d6
Create Date: 2026-06-06

Merge migration. Combines the test-DB branch (e6f7a8b9c0d1) and the
production-DB branch (e1f2a3b4c5d6) back into a single chain.
No schema changes. After this point, both databases follow the same path.
"""

from typing import Sequence, Union

revision = "d5e6f7a8b9c0"
down_revision: Union[str, Sequence[str], None] = ("e6f7a8b9c0d1", "e1f2a3b4c5d6")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
