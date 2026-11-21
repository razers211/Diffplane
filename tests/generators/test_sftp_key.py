"""The image server's host key on the switches, in the form V300 stores a typed RSA peer key.

The fixture is a throwaway 3072-bit RSA public key in the exact layout V300 uses when it stores a
typed RSA peer key: our generator must write exactly that text for that key, or every diff would
show it changed."""

import base64
import struct
from pathlib import Path

import pytest

from generators.sftp_key import key_code
from tests.generators.harness import devices_from_fabric, patch_to, render

ROOT = Path(__file__).resolve().parents[2]
ADDRESS = "198.51.100.2"

MODULUS = """\
    00ABB5DE FD204B77 17E506DF E72D41AE C0124520 03DA8E94 0DBEF748 86A38036
    C7581C05 64AB0A93 6BE9497D 624D8AB0 7023F75D FD30CD66 86DB0909 6B470D7A
    A02932BA BE97BB4F 3FC46FF8 0F0F2CC3 0B4F6E4E A7733EAA B4E1B011 5E7ED818
    085B076A 1C9F6D13 6840D7B9 41F00952 3B6809D5 F5B80D86 043795DB 84ED9D1C
    0E6E7575 E8B7AAB2 248E131F 7C926587 C2948922 0110ABDE 81BA7837 17A513A8
    3E5DD28A 3286425D E24D160D 75589C3D D8ECD2F2 9EB633E0 AA144C77 97BEC75D
    AEF8F421 F844DBA9 DA8FE9CD 243E6A6F 3BD7806E 494380D9 8C96081C 555B2520
    E2A515D3 AAC9E4AA C0CE949D 0AAEC386 FA6051BA 0E7E2373 85A40921 698564EE
    AF423C3A 0B9437C7 BAD90926 2EB39925 3688F42F 4768D51D 206B65E0 C9ADD1EE
    CE502352 2A48C0A4 73D9EC9E FD2FA5AB 31A33620 21A635DC EEF92BF0 5E65FDCB
    F0627F1B 4DE0F2FD C14AF426 B1213078 4CCBF7E5 AC729A24 38EE4657 75147D7F
    7DA1758C DBA15CF9 5822AC61 BA7868EC D638FF7C F648278F 8F1C5FE8 8A88E493
    7F
"""


def stored(name: str) -> str:
    """The block as `display current-configuration` shows it on leaf1."""
    return (f"rsa peer-public-key {name}\n public-key-code begin\n 3082018A\n  02820181\n{MODULUS}"
            "  0203\n    010001\n public-key-code end\n peer-public-key end\n")


def openssh() -> str:
    n = bytes.fromhex("".join(MODULUS.split()))
    e = bytes.fromhex("010001")
    blob = b"".join(struct.pack(">I", len(f)) + f for f in (b"ssh-rsa", e, n))
    return "ssh-rsa " + base64.b64encode(blob).decode()


def test_key_code_is_the_stored_form():
    sequence, integers = key_code(openssh())
    assert sequence == "3082018A"
    assert integers == [("02820181", [line.strip() for line in MODULUS.splitlines()]), ("0203", ["010001"])]


@pytest.fixture
def leaf1(tmp_path, monkeypatch):
    (tmp_path / "sftp_host_key.pub").write_text(openssh() + "\n")
    monkeypatch.setenv("TLS_PUBLIC_DIR", str(tmp_path))
    device = {d.hostname: d for d in devices_from_fabric(ROOT / "tests" / "fixtures" / "example-small.yaml")}["leaf1"]
    monkeypatch.delenv("SFTP_ADDRESS", raising=False)
    without = render(device)
    monkeypatch.setenv("SFTP_ADDRESS", ADDRESS)
    return device, without


PEER = f"ssh client peer {ADDRESS} assign rsa-key {ADDRESS}\n"
HAND_MADE = stored("172.26.10.45") + "ssh client peer 172.26.10.45 assign rsa-key 172.26.10.45\n"


def test_the_stored_key_gives_an_empty_diff_and_the_hand_made_key_is_left_alone(leaf1):
    device, without = leaf1
    assert patch_to(without + stored(ADDRESS) + PEER + HAND_MADE, device) == ""


def test_adding_types_the_key_then_its_peer_line(leaf1):
    device, without = leaf1
    lines = [line.strip() for line in patch_to(without + HAND_MADE, device).splitlines()]
    assert lines[0] == f"rsa peer-public-key {ADDRESS}" and "public-key-code begin" in lines
    assert lines.index("peer-public-key end") < lines.index(PEER.strip())
    # sent flat (rulebook.huawei.peer_key): a `quit` inside the key view would be read as hex
    assert lines[1:5] == ["public-key-code begin", "3082018A", "02820181", MODULUS.split("\n")[0].strip()]
    assert "quit" not in lines and lines[-3:] == ["public-key-code end", "peer-public-key end", PEER.strip()]
    assert "172.26.10.45" not in " ".join(lines)


def test_unset_address_removes_nothing(leaf1, monkeypatch):
    device, without = leaf1
    monkeypatch.delenv("SFTP_ADDRESS")
    assert patch_to(without + stored(ADDRESS) + PEER + HAND_MADE, device) == ""


def test_a_new_key_replaces_the_block(leaf1):
    device, without = leaf1
    old = stored(ADDRESS).replace("00ABB5DE", "00ABB5D0")
    lines = [line.strip() for line in patch_to(without + old + PEER, device).splitlines()]
    assert lines[0] == f"rsa peer-public-key {ADDRESS}" and MODULUS.split("\n")[0].strip() in lines
    assert PEER.strip() not in lines and "quit" not in lines   # the peer line stays; only the key is retyped


def test_a_missing_key_file_fails_instead_of_removing(leaf1, tmp_path):
    device, _ = leaf1
    (tmp_path / "sftp_host_key.pub").unlink()
    with pytest.raises(Exception, match="SftpServerKey"):
        render(device)
