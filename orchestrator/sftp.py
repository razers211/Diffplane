"""The platform's SFTP server's host key and one-time accounts.

The switches pull software images from `python -m sftpd`. Its RSA host key lives next to the TLS
certificate (TLS_DIR: HA copies that folder to both servers, so both present the same key); its
public half goes to TLS_PUBLIC_DIR, where the `SftpServerKey` generator reads it to write onto every
switch. One account per upgrade of one switch: a random login and password, only that upgrade's
files, an expiry; written to PLATFORM_SHARED (the server re-reads it at every login), removed when
the download is done. Only the password's SHA-256 is written.
"""

import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path

KEY, PUBLIC = "sftp_host_key", "sftp_host_key.pub"
ACCOUNTS = "sftp-accounts.json"
KEY_BITS = 4096   # the switch's stored form of a 4096-bit key was read on the console
ACCOUNT_SECONDS = 3600


def ensure_host_key(tls_dir: str | Path | None = None, public_dir: str | Path | None = None) -> tuple[str, bool]:
    """Make the host key if missing, publish its public half; (OpenSSH SHA256 fingerprint, made)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key_path = Path(tls_dir or os.environ.get("TLS_DIR") or "/tls") / KEY
    made = not key_path.exists()
    if made:
        key = rsa.generate_private_key(public_exponent=65537, key_size=KEY_BITS)
        tmp = key_path.with_name(KEY + ".tmp")
        tmp.touch(mode=0o600)
        # paramiko reads the traditional "BEGIN RSA PRIVATE KEY" form
        tmp.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                          serialization.NoEncryption()))
        os.replace(tmp, key_path)
    key = serialization.load_pem_private_key(key_path.read_bytes(), None)
    public = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
    target = Path(public_dir or os.environ.get("TLS_PUBLIC_DIR") or "/tls-public") / PUBLIC
    tmp = target.with_name(PUBLIC + ".tmp")
    tmp.write_bytes(public + b"\n")
    os.replace(tmp, target)
    return fingerprint(public.decode()), made


def fingerprint(openssh_public: str) -> str:
    blob = base64.b64decode(openssh_public.split()[1])
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def _hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


class Accounts:
    def __init__(self, shared: str | Path | None = None):
        self.path = Path(shared or os.environ.get("PLATFORM_SHARED") or "/shared") / ACCOUNTS

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            return {}

    def _write(self, accounts: dict) -> None:
        now = time.time()
        accounts = {u: a for u, a in accounts.items() if a["expires"] > now}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(accounts))
        tmp.chmod(0o644)   # the server runs as the same user; only hashes inside
        tmp.replace(self.path)

    def grant(self, files: list[str], seconds: float = ACCOUNT_SECONDS) -> tuple[str, str]:
        """A new account that may read only these files (names in the image store)."""
        login = "up" + secrets.token_hex(6)
        # letters and digits only: it is typed on the switch's command line (1-128 characters)
        password = secrets.token_urlsafe(24).replace("-", "a").replace("_", "b")
        accounts = self._read()
        accounts[login] = {"password_sha256": _hash(password), "files": sorted(files),
                           "expires": time.time() + seconds}
        self._write(accounts)
        return login, password

    def revoke(self, login: str) -> None:
        accounts = self._read()
        accounts.pop(login, None)
        self._write(accounts)

    def check(self, login: str, password: str) -> list[str] | None:
        """The files this login may read, or None (unknown, wrong password, expired)."""
        account = self._read().get(login)
        if not account or account["expires"] < time.time():
            return None
        if not secrets.compare_digest(account["password_sha256"], _hash(password)):
            return None
        return account["files"]


def progress(login: str, directory: str | Path | None = None) -> dict | None:
    """How far the image server has sent this login's file (written by sftpd): {file, bytes, size, at}."""
    path = Path(directory or os.environ.get("SFTP_PROGRESS_DIR") or "/progress") / f"{login}.json"
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return None
