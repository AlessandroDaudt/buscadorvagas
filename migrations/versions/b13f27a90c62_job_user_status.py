"""add user disposition to jobs

Revision ID: b13f27a90c62
Revises: 8a0d8f2c4b91
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b13f27a90c62"
down_revision: str | None = "8a0d8f2c4b91"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.add_column(
            sa.Column("user_status", sa.String(length=30), nullable=False, server_default="discovered")
        )
        batch_op.create_index("ix_jobs_user_status", ["user_status"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_index("ix_jobs_user_status")
        batch_op.drop_column("user_status")
