"""unique_index_lead_scores_is_current

Revision ID: d0ed8a831df0
Revises: e731bb3a3768
Create Date: 2026-05-26 00:30:33.041740

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd0ed8a831df0'
down_revision: Union[str, Sequence[str], None] = 'e731bb3a3768'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX idx_lead_scores_one_current "
        "ON lead_scores(company_id) WHERE is_current = TRUE"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_lead_scores_one_current")
