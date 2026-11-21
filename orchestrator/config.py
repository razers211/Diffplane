import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    netbox_url: str
    netbox_token: str
    netbox_timeout: float = 5.0
    # Unset: the topology is served from NetBox alone, every link unverified.
    prometheus_url: str | None = None
    # Platform database (jobs, diff snapshots). Unset: job endpoints answer 503.
    database_url: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        # Same variable names as Annet's NetBox adapter, so one environment serves both.
        missing = [name for name in ("NETBOX_URL", "NETBOX_TOKEN") if not os.environ.get(name)]
        if missing:
            raise RuntimeError(f"missing required environment variables: {', '.join(missing)}")
        return cls(
            netbox_url=os.environ["NETBOX_URL"].rstrip("/"),
            netbox_token=os.environ["NETBOX_TOKEN"],
            netbox_timeout=float(os.environ.get("NETBOX_TIMEOUT", "5")),
            prometheus_url=os.environ.get("PROMETHEUS_URL") or None,
            database_url=os.environ.get("DATABASE_URL") or None,
        )
