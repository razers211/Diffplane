"""The image server: one-time accounts, read-only, each login sees only its own files."""

import socket
import threading
import time

import pytest

from orchestrator import sftp

paramiko = pytest.importorskip("paramiko")


def test_account_reads_only_its_files_and_expires(tmp_path):
    accounts = sftp.Accounts(tmp_path)
    login, password = accounts.grant(["a.cc", "a.PAT"])
    assert password.isalnum() and password not in (tmp_path / sftp.ACCOUNTS).read_text()
    assert accounts.check(login, password) == ["a.PAT", "a.cc"]
    assert accounts.check(login, password + "x") is None and accounts.check("nobody", password) is None
    accounts.revoke(login)
    assert accounts.check(login, password) is None
    login, password = accounts.grant(["a.cc"], seconds=-1)
    assert accounts.check(login, password) is None


def test_host_key_made_once_and_published(tmp_path):
    fp, made = sftp.ensure_host_key(tmp_path, tmp_path)
    again, made_again = sftp.ensure_host_key(tmp_path, tmp_path)
    assert made and not made_again and fp == again and fp.startswith("SHA256:")
    public = (tmp_path / sftp.PUBLIC).read_text()
    assert public.startswith("ssh-rsa ") and sftp.fingerprint(public) == fp
    assert (tmp_path / sftp.KEY).stat().st_mode & 0o077 == 0


@pytest.fixture
def server(tmp_path, monkeypatch):
    import sftpd.__main__ as server

    images = tmp_path / "images"
    images.mkdir()
    (images / "a.cc").write_bytes(b"system" * 1000)
    (images / "b.cc").write_bytes(b"other")
    monkeypatch.setattr(server, "IMAGES", images)
    (tmp_path / "progress").mkdir()
    monkeypatch.setattr(server, "PROGRESS", tmp_path / "progress")
    sftp.ensure_host_key(tmp_path, tmp_path)
    key = paramiko.RSAKey.from_private_key_file(str(tmp_path / sftp.KEY))
    accounts = sftp.Accounts(tmp_path)
    listener = socket.create_server(("127.0.0.1", 0))

    def accept():
        while True:
            client, _ = listener.accept()
            threading.Thread(target=server.serve, args=(client, key, accounts), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    yield listener.getsockname()[1], accounts, key, tmp_path / "progress"
    listener.close()


def connect(port, login, password, key):
    transport = paramiko.Transport(("127.0.0.1", port))
    transport.connect(hostkey=key, username=login, password=password)   # the pinned key, as a switch does
    return transport, paramiko.SFTPClient.from_transport(transport)


def test_download_of_the_granted_file_only(server):
    port, accounts, key, progress_dir = server
    login, password = accounts.grant(["a.cc"])
    transport, client = connect(port, login, password, key)
    try:
        assert client.listdir(".") == ["a.cc"]
        with client.open("/a.cc") as f:
            assert f.read() == b"system" * 1000
        seen = sftp.progress(login, progress_dir)   # how far the upgrade page shows it
        assert seen["file"] == "a.cc" and seen["bytes"] == seen["size"] == 6000
        assert client.stat("a.cc").st_size == 6000
        with pytest.raises(IOError):
            client.open("b.cc")   # in the store, not this account's
        with pytest.raises(IOError):
            client.open("../images/b.cc")
        with pytest.raises(IOError):
            client.open("a.cc", "w")
        with pytest.raises(IOError):
            client.remove("a.cc")
    finally:
        transport.close()
    for _ in range(50):   # removed once the session ends
        if sftp.progress(login, progress_dir) is None:
            break
        time.sleep(0.02)
    assert sftp.progress(login, progress_dir) is None


def test_wrong_password_and_revoked_login_are_refused(server):
    port, accounts, key, _ = server
    login, password = accounts.grant(["a.cc"])
    with pytest.raises(paramiko.AuthenticationException):
        connect(port, login, "wrong", key)
    accounts.revoke(login)
    time.sleep(0.05)
    with pytest.raises(paramiko.AuthenticationException):
        connect(port, login, password, key)
