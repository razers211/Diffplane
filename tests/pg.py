"""A throwaway PostgreSQL for the tests.

pgserver where it has a build (glibc), otherwise the system's own binaries (the Alpine image:
`apk add postgresql17`). Without either the database tests skip, except in CI, where they fail:
a skipped database test must never look like a pass.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

try:
    import pgserver
except ImportError:
    pgserver = None


def bin_dir() -> Path | None:
    if pgserver is not None:
        return Path(pgserver.__file__).parent / "pginstall" / "bin"
    initdb = shutil.which("initdb") or next(iter(sorted(Path("/usr/libexec").glob("postgresql*/initdb"))), None)
    return Path(initdb).parent if initdb else None


def require() -> None:
    """Call at module level in tests that need PostgreSQL."""
    if bin_dir() is None:
        if os.environ.get("CI"):
            pytest.fail("no PostgreSQL for the database tests (pgserver, or postgresql binaries)", pytrace=False)
        pytest.skip("no PostgreSQL (install pgserver, or the postgresql binaries)", allow_module_level=True)


class Server:
    """initdb, then a server on a Unix socket only, trusting local connections."""

    def __init__(self, root: Path):
        self.bin, self.data, self.socket = bin_dir(), root / "db", root / "s"
        self.socket.mkdir()
        self._run("initdb", "-D", self.data, "-U", "postgres", "--auth=trust", "-E", "UTF8")
        self._run("pg_ctl", "-D", self.data, "-l", root / "log", "-w", "start",
                  "-o", f"-k {self.socket} -c listen_addresses=''")

    def _run(self, tool, *args) -> str:
        return subprocess.run([self.bin / tool, *map(str, args)], check=True, capture_output=True, text=True).stdout

    def psql(self, sql: str) -> str:
        # Through stdin, as pgserver does, so meta-commands such as \c work inside `sql`.
        return subprocess.run([self.bin / "psql", "-h", str(self.socket), "-U", "postgres", "-d", "postgres",
                               "-tA", "-v", "ON_ERROR_STOP=1"],
                              input=sql, check=True, capture_output=True, text=True).stdout

    def stop(self) -> None:
        subprocess.run([self.bin / "pg_ctl", "-D", self.data, "-m", "fast", "stop"], capture_output=True)


def start(root: Path):
    """(server with .psql(sql), its socket directory). Stop a `Server` when done; pgserver stops itself."""
    require()
    if pgserver is not None:
        from urllib.parse import parse_qs, urlparse

        server = pgserver.get_server(root, cleanup_mode="stop")
        return server, parse_qs(urlparse(server.get_uri()).query)["host"][0]
    server = Server(root)
    return server, str(server.socket)
