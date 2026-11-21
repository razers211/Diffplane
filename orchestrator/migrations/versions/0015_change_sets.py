"""Staged changes: intent writes saved for a change window.

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "change_sets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("site", sa.String(100), nullable=False, index=True),
        sa.Column("author", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("steps", sa.JSON, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("change_sets")
