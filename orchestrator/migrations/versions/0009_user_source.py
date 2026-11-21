"""Where an account comes from: local, or the directory that granted it (ldap, radius).

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("source", sa.String(16), nullable=False, server_default="local"))


def downgrade() -> None:
    op.drop_column("users", "source")
