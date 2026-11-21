"""The backup script against a real PostgreSQL: dump, restore, retention."""

import os
import subprocess
import time
from pathlib import Path

import pytest

from tests import pg
from tests.orchestrator.conftest import postgres  # noqa: F401  (session fixture)

pg.require()
SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "backup" / "backup.sh"
BIN = pg.bin_dir()


def psql(socket_dir, database, sql) -> str:
    return subprocess.run([BIN / "psql", "-h", socket_dir, "-U", "postgres", "-d", database, "-tAc", sql],
                          check=True, capture_output=True, text=True).stdout.strip()


def test_backup_restores_and_old_backups_are_pruned(postgres, tmp_path):  # noqa: F811
    server, socket_dir = postgres
    for name, value in (("bk_netbox", "intent"), ("bk_platform", "audit")):
        server.psql(f"CREATE DATABASE {name};")
        psql(socket_dir, name, f"CREATE TABLE t (v text); INSERT INTO t VALUES ('{value}');")
    media = tmp_path / "media"
    (media / "image-attachments").mkdir(parents=True)
    (media / "image-attachments" / "rack.png").write_bytes(b"png")
    backups = tmp_path / "backups"
    old = backups / "20260901T000000Z"
    old.mkdir(parents=True)
    os.utime(old, (time.time() - 20 * 86400,) * 2)

    env = {
        "PATH": f"{BIN}:{os.environ['PATH']}", "BACKUP_DIR": str(backups), "NETBOX_MEDIA_DIR": str(media),
        "NETBOX_DB_HOST": socket_dir, "NETBOX_DB_USER": "postgres", "NETBOX_DB_NAME": "bk_netbox",
        "PLATFORM_DB_HOST": socket_dir, "PLATFORM_DB_USER": "postgres", "PLATFORM_DB_NAME": "bk_platform",
    }
    result = subprocess.run(["sh", SCRIPT, "once"], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

    (made,) = [p for p in backups.iterdir() if p.name != old.name]
    assert not old.exists(), "a backup older than 14 days must be pruned"
    assert sorted(p.name for p in made.iterdir()) == ["netbox-media.tar.gz", "netbox.dump", "platform.dump"]

    # Restore as docs/operations.md says: into an empty database, then read the data back.
    server.psql("CREATE DATABASE bk_restored;")
    subprocess.run([BIN / "pg_restore", "-h", socket_dir, "-U", "postgres", "-d", "bk_restored", "--no-owner",
                    made / "netbox.dump"], check=True)
    assert psql(socket_dir, "bk_restored", "SELECT v FROM t") == "intent"
    listing = subprocess.run(["tar", "-tzf", made / "netbox-media.tar.gz"], capture_output=True, text=True).stdout
    assert "./image-attachments/rack.png" in listing


def test_backup_service_is_in_compose():
    import yaml

    compose = yaml.safe_load((SCRIPT.parents[1] / "compose.yaml").read_text())
    backup = compose["services"]["backup"]
    assert "./backups:/backups" in backup["volumes"]
    assert "netbox-media:/netbox-media:ro" in backup["volumes"]
    assert backup["environment"]["BACKUP_KEEP_DAYS"] == "14"
