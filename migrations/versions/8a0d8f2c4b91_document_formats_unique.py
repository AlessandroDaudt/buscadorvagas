"""allow multiple generated document formats per version

Revision ID: 8a0d8f2c4b91
Revises: df89d4c242bb
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "8a0d8f2c4b91"
down_revision: str | None = "df89d4c242bb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_COLUMNS = ["job_id", "document_type", "language", "version"]
_NEW_COLUMNS = ["job_id", "document_type", "language", "file_format", "version"]
_CONVENTION = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def _constraint_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        return "generated_documents_job_id_document_type_language_version_key"
    return "uq_generated_documents_job_id"


def upgrade() -> None:
    with op.batch_alter_table(
        "generated_documents", recreate="always", naming_convention=_CONVENTION
    ) as batch_op:
        batch_op.drop_constraint(_constraint_name(), type_="unique")
        batch_op.create_unique_constraint("uq_generated_documents_format_version", _NEW_COLUMNS)


def downgrade() -> None:
    with op.batch_alter_table(
        "generated_documents", recreate="always", naming_convention=_CONVENTION
    ) as batch_op:
        batch_op.drop_constraint("uq_generated_documents_format_version", type_="unique")
        batch_op.create_unique_constraint("uq_generated_documents_job_id", _OLD_COLUMNS)
