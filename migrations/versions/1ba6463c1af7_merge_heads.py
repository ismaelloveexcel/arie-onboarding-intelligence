"""merge_heads

Revision ID: 1ba6463c1af7
Revises: c7d8e9f0a1b2, d9e8f7a6b5c4
Create Date: 2026-06-04 14:38:36.932639

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1ba6463c1af7'
down_revision: Union[str, Sequence[str], None] = ('c7d8e9f0a1b2', 'd9e8f7a6b5c4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
