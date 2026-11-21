"""Intent documents: every version of each NetBox config context, kept in the platform database.

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "intent_documents",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, index=True),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("written_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sites", sa.JSON, nullable=False),
        sa.Column("data", sa.JSON, nullable=True),
        sa.UniqueConstraint("name", "version"),
    )


def downgrade() -> None:
    op.drop_table("intent_documents")
