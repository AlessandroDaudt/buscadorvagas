"""structured learning feedback for jobs and company proposals

Revision ID: e91c2a7f4d30
Revises: c42df01a7e21
Create Date: 2026-07-20 20:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e91c2a7f4d30"
down_revision: Union[str, None] = "c42df01a7e21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(
            sa.Column("feedback_reasons", sa.JSON(), nullable=False, server_default="[]")
        )
        batch.add_column(sa.Column("feedback_note", sa.Text(), nullable=True))
    with op.batch_alter_table("portal_discovery_proposals") as batch:
        batch.add_column(
            sa.Column("feedback_reasons", sa.JSON(), nullable=False, server_default="[]")
        )
        batch.add_column(sa.Column("feedback_note", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("portal_discovery_proposals") as batch:
        batch.drop_column("feedback_note")
        batch.drop_column("feedback_reasons")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("feedback_note")
        batch.drop_column("feedback_reasons")
