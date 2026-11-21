"""The platform's SFTP server for switch software images: `python -m sftpd`.

Read-only. A login is one of the one-time accounts the orchestrator writes (orchestrator/sftp.py,
re-read at every login); it sees only its own files, flat, from the image store (`IMAGES_DIR`).
Password authentication only; no shell, no exec, no writes, no other subsystem. The host key is
the one the orchestrator made (TLS_DIR), the key every switch is told to expect.
"""

import json
import logging
import os
import socket
import threading
import time
from pathlib import Path

import paramiko
from paramiko.sftp import SFTP_NO_SUCH_FILE, SFTP_PERMISSION_DENIED

from orchestrator.sftp import KEY, Accounts

log = logging.getLogger("sftpd")
IMAGES = Path(os.environ.get("IMAGES_DIR", "/images"))
# How far each login's download is, for the upgrade page: one small file per login, rewritten
# every PROGRESS_SECONDS while it reads, removed when its session ends.
PROGRESS = Path(os.environ.get("SFTP_PROGRESS_DIR", "/progress"))
PROGRESS_SECONDS = 2.0


def report(login: str, name: str, sent: int, size: int) -> None:
    try:
        tmp = PROGRESS / f".{login}.tmp"
        tmp.write_text(json.dumps({"file": name, "bytes": sent, "size": size, "at": time.time()}))
        tmp.replace(PROGRESS / f"{login}.json")
    except OSError as exc:   # progress is a convenience: the download goes on
        log.debug("progress not written: %s", exc)


def allowed(files: list[str], path: str) -> Path | None:
    """The image a client path names, if that login may read it ('x.cc', '/x.cc', 'flash:/x.cc' -> x.cc)."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    return IMAGES / name if name in files and (IMAGES / name).is_file() else None


class Login(paramiko.ServerInterface):
    def __init__(self, accounts: Accounts):
        self.accounts = accounts
        self.files: list[str] = []
        self.login = ""

    def get_allowed_auths(self, username):
        return "password"

    def check_auth_password(self, username, password):
        files = self.accounts.check(username, password)
        if files is None:
            log.warning("refused login %r", username)
            return paramiko.AUTH_FAILED
        self.files, self.login = files, username
        log.info("login %s (%d files)", username, len(files))
        return paramiko.AUTH_SUCCESSFUL

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


class Handle(paramiko.SFTPHandle):
    login = ""
    size = 0
    sent = 0
    reported = 0.0

    def stat(self):
        return paramiko.SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))

    def read(self, offset, length):
        data = super().read(offset, length)
        if isinstance(data, bytes):
            self.sent = max(self.sent, offset + len(data))
            if time.monotonic() - self.reported > PROGRESS_SECONDS or self.sent >= self.size:
                self.reported = time.monotonic()
                report(self.login, Path(self.filename).name, self.sent, self.size)
        return data


class ReadOnly(paramiko.SFTPServerInterface):
    def __init__(self, server: Login, *args, **kwargs):
        super().__init__(server, *args, **kwargs)
        self.files, self.login = server.files, server.login

    def _attrs(self, path: Path) -> paramiko.SFTPAttributes:
        attrs = paramiko.SFTPAttributes.from_stat(path.stat())
        attrs.filename = path.name
        return attrs

    def list_folder(self, path):
        return [self._attrs(IMAGES / f) for f in self.files if (IMAGES / f).is_file()]

    def stat(self, path):
        if path.rstrip("/") in ("", "."):
            return paramiko.SFTPAttributes.from_stat(IMAGES.stat())
        found = allowed(self.files, path)
        return self._attrs(found) if found else SFTP_NO_SUCH_FILE

    lstat = stat

    def canonicalize(self, path):
        return "/" + path.replace("\\", "/").rsplit("/", 1)[-1] if path.strip("/.") else "/"

    def open(self, path, flags, attr):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC):
            return SFTP_PERMISSION_DENIED
        found = allowed(self.files, path)
        if not found:
            return SFTP_NO_SUCH_FILE
        handle = Handle(flags)
        handle.readfile = open(found, "rb")
        handle.filename = str(found)
        handle.login, handle.size = self.login, found.stat().st_size
        return handle

    def remove(self, path):
        return SFTP_PERMISSION_DENIED

    rename = mkdir = rmdir = setstat = chattr = symlink = posix_rename = lambda self, *a: SFTP_PERMISSION_DENIED


def serve(client: socket.socket, key: paramiko.PKey, accounts: Accounts) -> None:
    transport = paramiko.Transport(client)
    login = Login(accounts)
    try:
        transport.add_server_key(key)
        transport.set_subsystem_handler("sftp", paramiko.SFTPServer, ReadOnly)
        transport.start_server(server=login)
        while transport.is_active():   # the SFTP subsystem runs in the transport's own thread
            transport.join(30)
    except Exception as exc:   # one bad client never stops the server
        log.warning("session ended: %s", exc)
    finally:
        transport.close()
        if login.login:
            (PROGRESS / f"{login.login}.json").unlink(missing_ok=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("paramiko").setLevel(logging.WARNING)
    key_path = Path(os.environ.get("TLS_DIR", "/tls")) / KEY
    while not key_path.exists():   # the orchestrator makes it at its start
        log.info("waiting for the host key %s", key_path)
        time.sleep(5)
    key = paramiko.RSAKey.from_private_key_file(str(key_path))
    accounts = Accounts()
    port = int(os.environ.get("SFTP_LISTEN_PORT", "2222"))
    listener = socket.create_server(("", port), reuse_port=False)
    log.info("listening on %d, images in %s", port, IMAGES)
    while True:
        client, address = listener.accept()
        log.info("connection from %s", address[0])
        threading.Thread(target=serve, args=(client, key, accounts), daemon=True).start()


if __name__ == "__main__":
    main()
