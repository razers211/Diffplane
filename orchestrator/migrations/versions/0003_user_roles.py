"""User roles: viewer < operator < approver.

Users from before roles become approvers, so nothing they could do is taken away;
users created afterwards start as viewers unless a role is given.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("role", sa.String(16), nullable=False, server_default="approver"))
    op.alter_column("users", "role", server_default="viewer")


def downgrade() -> None:
    op.drop_column("users", "role")
