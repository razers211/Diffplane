"""The ZTP TFTP server (deploy/ztp/tftpd.py): each address reads only its own folder."""

import importlib.util
import socket
import struct
import threading
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, DEPLOY / "ztp" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tftpd = load("tftpd")


@pytest.fixture
def server(tmp_path):
    (tmp_path / "127.0.0.1").mkdir()
    (tmp_path / "127.0.0.1" / "ztp.ini").write_text("own file\n")
    (tmp_path / "127.0.0.1" / "big.cfg").write_bytes(b"x" * 1500)   # three blocks
    (tmp_path / "127.0.0.9").mkdir()
    (tmp_path / "127.0.0.9" / "other.cfg").write_text("secret of another switch\n")
    (tmp_path / "top.cfg").write_text("in the root\n")
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    ready, stop = threading.Event(), threading.Event()
    thread = threading.Thread(target=tftpd.serve, args=("127.0.0.1", str(tmp_path), port, ready, stop), daemon=True)
    thread.start()
    ready.wait(5)
    yield port
    stop.set()
    thread.join(5)


def get(port: int, name: str, source: str = "127.0.0.1") -> tuple[int, bytes]:
    """(0, data) or (error code, message), as a switch's TFTP client sees it."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((source, 0))
    sock.settimeout(5)
    sock.sendto(b"\0\1" + name.encode() + b"\0octet\0", ("127.0.0.1", port))
    data = b""
    while True:
        packet, addr = sock.recvfrom(4 + 512)
        opcode = struct.unpack("!H", packet[:2])[0]
        if opcode == 5:
            return struct.unpack("!H", packet[2:4])[0], packet[4:].rstrip(b"\0")
        data += packet[4:]
        sock.sendto(b"\0\4" + packet[2:4], addr)
        if len(packet) - 4 < 512:
            return 0, data


def test_a_switch_reads_its_own_files(server):
    assert get(server, "ztp.ini") == (0, b"own file\n")
    assert get(server, "big.cfg") == (0, b"x" * 1500)


def test_nothing_outside_its_own_folder(server):
    assert get(server, "other.cfg")[0] == tftpd.NOT_FOUND
    for name in ("127.0.0.9/other.cfg", "../127.0.0.9/other.cfg", "../top.cfg", "..", ".hidden", "a\\b"):
        assert get(server, name)[0] == tftpd.ACCESS, name


def test_an_address_without_a_folder_reads_nothing(server):
    # 127.0.0.2 is not registered: no folder of its own, so not even the root or a path.
    for name in ("ztp.ini", "top.cfg", "127.0.0.1/ztp.ini"):
        assert get(server, name, source="127.0.0.2")[0] == tftpd.ACCESS, name


def test_writes_are_refused(server):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5)
    sock.sendto(b"\0\2ztp.ini\0octet\0", ("127.0.0.1", server))
    packet, _ = sock.recvfrom(516)
    assert packet[:4] == b"\0\5\0\2"


def test_the_self_test_client_parses_the_same_server(server, capsys):
    client = load("selftest_client")
    assert client.tftp("127.0.0.1", "ztp.ini", port=server) == 0
    # The server runs in this process too: leave out its own log lines.
    out = [line for line in capsys.readouterr().out.splitlines() if not line.startswith("tftpd: ")]
    assert out == ["own file"]
