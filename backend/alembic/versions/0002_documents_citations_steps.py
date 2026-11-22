"""documents table; citations and agent steps on messages

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-22
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("citations", sa.JSON(), nullable=True))
    op.add_column("messages", sa.Column("steps", sa.JSON(), nullable=True))

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("filename", sa.String(300), nullable=False),
        sa.Column("content_type", sa.String(150), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_path", sa.String(500), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_documents_conversation_id", "documents", ["conversation_id"])


def downgrade() -> None:
    op.drop_table("documents")
    op.drop_column("messages", "steps")
    op.drop_column("messages", "citations")
