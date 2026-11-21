"""Users, sessions, audit trail, deploy steps and job log.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, column in [
        ("requested_by", sa.String(100)),
        ("approved_by", sa.String(100)),
        ("approved_at", sa.DateTime(timezone=True)),
        ("generators", sa.Text),
    ]:
        op.add_column("jobs", sa.Column(name, column))

    op.create_table(
        "deploy_steps",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("job_id", sa.Integer, sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device", sa.String(100), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("patch", sa.Text),
        sa.Column("error", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_deploy_steps_job_id", "deploy_steps", ["job_id"])

    op.create_table(
        "job_log",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("job_id", sa.Integer, sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("device", sa.String(100)),
        sa.Column("level", sa.String(8), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
    )
    op.create_index("ix_job_log_job_id", "job_log", ["job_id"])

    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("username", sa.String(100), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("username", sa.String(100)),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("job_id", sa.Integer),
        sa.Column("detail", sa.Text),
    )
    op.create_index("ix_audit_events_at", "audit_events", ["at"])
    op.create_index("ix_audit_events_job_id", "audit_events", ["job_id"])


def downgrade() -> None:
    for table in ("audit_events", "sessions", "users", "job_log", "deploy_steps"):
        op.drop_table(table)
    for name in ("generators", "approved_at", "approved_by", "requested_by"):
        op.drop_column("jobs", name)
