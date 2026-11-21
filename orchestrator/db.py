"""Platform database: jobs and diff snapshots (PostgreSQL, schema via Alembic)."""

from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Job(Base):
    """A unit of work over one or more devices: kind "diff", "deploy" or "upgrade"."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    # running, done, failed
    status: Mapped[str] = mapped_column(String(16))
    site: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    # Deploy jobs: who asked, who approved and when, which generators (None: all).
    requested_by: Mapped[str | None] = mapped_column(String(100))
    approved_by: Mapped[str | None] = mapped_column(String(100))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generators: Mapped[str | None] = mapped_column(Text)
    # Deploy jobs: pre-deploy checks of each fabric, as when prepared or last approved.
    checks: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Upgrade jobs: the images asked for, {"system": id, "patch": id} (either may be None).
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Change windows: a job approved for a window starts at window_start, no new wave after window_end.
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    snapshots: Mapped[list["DiffSnapshot"]] = relationship(
        back_populates="job", order_by="DiffSnapshot.device", lazy="selectin"
    )
    steps: Mapped[list["DeployStep"]] = relationship(
        back_populates="job", order_by="DeployStep.position", lazy="selectin"
    )


class DiffSnapshot(Base):
    """`annet diff` output for one device, secrets masked."""

    __tablename__ = "diff_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    device: Mapped[str] = mapped_column(String(100), index=True)
    # pending, ok, error
    status: Mapped[str] = mapped_column(String(16))
    has_changes: Mapped[bool | None] = mapped_column(Boolean)
    diff: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped[Job] = relationship(back_populates="snapshots")


class DeployStep(Base):
    """One device of a deploy job, in rollout order."""

    __tablename__ = "deploy_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    device: Mapped[str] = mapped_column(String(100))
    position: Mapped[int] = mapped_column(Integer)
    # pending, diffed, unchanged, deploying, verifying, verified, failed, skipped
    status: Mapped[str] = mapped_column(String(16))
    # The patch shown for approval (secrets masked). Deploy refuses if it changed.
    patch: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped[Job] = relationship(back_populates="steps")


class JobLog(Base):
    """Live log of a job, shown in the GUI."""

    __tablename__ = "job_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    device: Mapped[str | None] = mapped_column(String(100))
    level: Mapped[str] = mapped_column(String(8))
    message: Mapped[str] = mapped_column(Text)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="viewer", server_default="viewer")
    # Fabrics a viewer or operator is limited to: None reaches every fabric; else
    # {site slug: may approve deploys there}.
    fabrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # local, or the directory that granted the account (ldap, radius): its role and fabrics
    # are rewritten at every sign-in, it has no password here.
    source: Mapped[str] = mapped_column(String(16), default="local", server_default="local")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Session(Base):
    """A login session. Only a hash of the cookie token is stored."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(lazy="joined")


class Setting(Base):
    """A platform setting set from the GUI; replaces its .env default. Secrets included:
    the platform database is where the platform keeps its secrets (as NetBox keeps its token)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_by: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditEvent(Base):
    """Who did what: logins, deploy requests, approvals, rollouts."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    username: Mapped[str | None] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(50))
    job_id: Mapped[int | None] = mapped_column(Integer, index=True)
    detail: Mapped[str | None] = mapped_column(Text)


class ConfigVersion(Base):
    """A switch's running config at one moment, encrypted (orchestrator/backups.py)."""

    __tablename__ = "config_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device: Mapped[str] = mapped_column(String(100), index=True)
    site: Mapped[str | None] = mapped_column(String(100))
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    reason: Mapped[str] = mapped_column(String(20))          # scheduled, deploy, manual
    job_id: Mapped[int | None] = mapped_column(Integer)      # the verified deploy, for reason "deploy"
    requested_by: Mapped[str | None] = mapped_column(String(100))
    digest: Mapped[str] = mapped_column(String(64))          # of the config without its comment lines
    lines: Mapped[int] = mapped_column(Integer)
    config: Mapped[bytes] = mapped_column(LargeBinary)       # Fernet token of the raw running config


class IntentSnapshot(Base):
    """A fabric's GUI-written intent when one of its deploys was verified (orchestrator/intent_snapshots.py)."""

    __tablename__ = "intent_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site: Mapped[str] = mapped_column(String(100), index=True)
    job_id: Mapped[int | None] = mapped_column(Integer)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    digest: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSON)


class ChangeSet(Base):
    """Intent changes saved for a change window (orchestrator/drafts.py): the recorded intent
    writes, applied to NetBox only at the window's start."""

    __tablename__ = "change_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site: Mapped[str] = mapped_column(String(100), index=True)
    author: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    status: Mapped[str] = mapped_column(String(16), default="open")   # open, scheduled, applied, discarded
    steps: Mapped[list] = mapped_column(JSON, default=list)   # [{method, path, body, user, at}]


class IntentDocument(Base):
    """One version of an intent document (a NetBox config context: fabric settings, segments,
    routing, overrides, DC links), kept for good (orchestrator/intent_store.py). `data` None: deleted."""

    __tablename__ = "intent_documents"
    __table_args__ = (UniqueConstraint("name", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[int] = mapped_column(Integer)
    written_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    sites: Mapped[list] = mapped_column(JSON, default=list)   # the site slugs it is assigned to
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    written_by: Mapped[str | None] = mapped_column(String(100))   # an account, or "platform"; None before 0017


class Event(Base):
    """A state change the event watcher saw (orchestrator/events.py), kept 90 days."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    site: Mapped[str | None] = mapped_column(String(100), index=True)
    device: Mapped[str | None] = mapped_column(String(100), index=True)
    kind: Mapped[str] = mapped_column(String(20))            # link, bgp, ospf, mlag, hardware, optic, polling, alert, drift, health
    subject: Mapped[str] = mapped_column(String(200))        # what changed: a port, a peer, a module, an alert
    state: Mapped[str] = mapped_column(String(60))
    previous: Mapped[str | None] = mapped_column(String(60))
    severity: Mapped[str] = mapped_column(String(12))        # problem, recovered, change
    detail: Mapped[str | None] = mapped_column(Text)


class TrafficHour(Base):
    """One port's or trunk's traffic in one direction over one hour (orchestrator/capacity.py),
    kept 13 months."""

    __tablename__ = "traffic_hourly"
    __table_args__ = (UniqueConstraint("device", "interface", "direction", "hour"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device: Mapped[str] = mapped_column(String(100))
    interface: Mapped[str] = mapped_column(String(100))
    direction: Mapped[str] = mapped_column(String(3))         # in, out
    hour: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)   # the hour's start
    avg_bps: Mapped[float | None] = mapped_column(Float)
    p95_bps: Mapped[float | None] = mapped_column(Float)


class SoftwareImage(Base):
    """A switch software image in the image store (orchestrator/images.py)."""

    __tablename__ = "software_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)   # the file name, kept on the switch's flash too
    kind: Mapped[str] = mapped_column(String(10))                 # system, patch
    release: Mapped[str] = mapped_column(String(40), index=True)  # from Huawei's file name
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))               # computed at upload
    sha256_typed: Mapped[bool] = mapped_column(Boolean, default=False)   # Huawei's was typed and matched
    uploaded_by: Mapped[str] = mapped_column(String(100))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


def make_engine(url: str, pooled: bool = True) -> AsyncEngine:
    """pooled=False for tests that use one engine from several event loops."""
    if not pooled:
        from sqlalchemy.pool import NullPool

        return create_async_engine(url, poolclass=NullPool)
    return create_async_engine(url, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)
