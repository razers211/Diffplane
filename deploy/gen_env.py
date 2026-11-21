"""Create deploy/.env from deploy/.env.example, filling every empty value with a random secret.

    python3 gen_env.py          a single install, or the first HA machine
    python3 gen_env.py --ha     also the HA cluster's shared secrets, added to an existing
                                .env if it lacks them; then copy that one .env to all three machines
"""

import base64
import secrets
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

GENERATORS = {
    "NETBOX_SECRET_KEY": lambda: secrets.token_urlsafe(50),
    # NetBox 4.4 (v1) API tokens are 40 characters.
    "NETBOX_TOKEN": lambda: secrets.token_hex(20),
    # A Fernet key: 32 random bytes, url-safe base64.
    "CONFIG_BACKUP_KEY": lambda: base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
}
# Left empty: real credentials only a person can supply, and settings whose empty value means
# "the default" (a random string there would break the service reading it: ANOMALY_*).
MANUAL = {"GNETCLI_DEV_LOGIN", "GNETCLI_DEV_PASSWORD", "SNMP_COMMUNITY", "COLLECTOR_LOGIN", "COLLECTOR_PASSWORD", "MLAG_DFS_PASSWORD",
          "ZTP_ADMIN_LOGIN", "ZTP_ADMIN_PASSWORD", "ZTP_DHCP_INTERFACE", "ZTP_DHCP_NETWORK", "ZTP_SERVER_ADDRESS",
          "ANOMALY_MIN_BPS", "ANOMALY_MIN_SHARE", "SFTP_ADDRESS", "ZTP_WINDOW_HOURS",
          "DEPLOY_ALLOWED_SITES"}   # empty: deploys off until an admin allows a fabric


def render(template: str) -> str:
    lines = []
    for line in template.splitlines():
        key, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#") and value == "" and key not in MANUAL:
            value = GENERATORS.get(key, lambda: secrets.token_urlsafe(24))()
            line = f"{key}={value}"
        lines.append(line)
    return "\n".join(lines) + "\n"


# Shared by the three HA machines. The VRRP password is cut to keepalived's 8 characters.
HA_SECRETS = {
    "PATRONI_SUPERUSER_PASSWORD": lambda: secrets.token_urlsafe(24),
    "PATRONI_REPLICATION_PASSWORD": lambda: secrets.token_urlsafe(24),
    "VRRP_PASSWORD": lambda: secrets.token_hex(4),
    "ETCD_TOKEN": lambda: "fabric-" + secrets.token_hex(8),
}


def add_ha(text: str) -> str:
    have = {line.partition("=")[0] for line in text.splitlines() if "=" in line and not line.lstrip().startswith("#")}
    extra = [f"{key}={make()}" for key, make in HA_SECRETS.items() if key not in have]
    if not extra:
        return text
    return text.rstrip("\n") + "\n\n# HA cluster: the same on all three machines.\n" + "\n".join(extra) + "\n"


def main() -> int:
    target = HERE / ".env"
    ha = "--ha" in sys.argv[1:]
    if target.exists() and not ha:
        print(f"{target} already exists; not overwriting", file=sys.stderr)
        return 1
    text = target.read_text() if target.exists() else render((HERE / ".env.example").read_text())
    if ha:
        text = add_ha(text)
    target.write_text(text)
    target.chmod(0o600)
    print(f"wrote {target}" + (" with the HA secrets: copy this same file to the other two machines" if ha else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
