"""persistent web tasks and reviewed resume versions

Revision ID: 8b7f9d2a6c10
Revises: b13f27a90c62
Create Date: 2026-07-20 15:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "8b7f9d2a6c10"
down_revision: Union[str, None] = "b13f27a90c62"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "web_tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_type", sa.String(length=50), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("message", sa.String(length=1000), nullable=False),
        sa.Column("payload_data", sa.JSON(), nullable=False),
        sa.Column("result_data", sa.JSON(), nullable=False),
        sa.Column("error", sa.String(length=1000), nullable=True),
        sa.Column("cancel_safe", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_web_tasks_task_type", "web_tasks", ["task_type"], unique=False)
    op.create_index("ix_web_tasks_state", "web_tasks", ["state"], unique=False)
    op.create_index(
        "ix_web_tasks_state_created", "web_tasks", ["state", "created_at"], unique=False
    )
    op.create_table(
        "resume_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("source_format", sa.String(length=20), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("extraction_method", sa.String(length=100), nullable=False),
        sa.Column("detected_sections", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("metadata_data", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("previous_version_id", sa.String(length=36), nullable=True),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["previous_version_id"], ["resume_versions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_resume_versions_active", "resume_versions", ["active"], unique=False)
    op.create_index("ix_resume_versions_created", "resume_versions", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_resume_versions_created", table_name="resume_versions")
    op.drop_index("ix_resume_versions_active", table_name="resume_versions")
    op.drop_table("resume_versions")
    op.drop_index("ix_web_tasks_state_created", table_name="web_tasks")
    op.drop_index("ix_web_tasks_state", table_name="web_tasks")
    op.drop_index("ix_web_tasks_task_type", table_name="web_tasks")
    op.drop_table("web_tasks")
