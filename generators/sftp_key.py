import base64
import os
import struct
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from annet.generators import PartialGenerator
from annet.generators.exceptions import GeneratorError

ADDRESS_ENV = "SFTP_ADDRESS"   # where the switches reach the image server (Settings; the southbound VIP in HA)
PUBLIC_KEY = "sftp_host_key.pub"   # written by the orchestrator (orchestrator/sftp.py)
WORDS_PER_LINE = 8


def address() -> str | None:
    return (os.environ.get(ADDRESS_ENV) or "").strip() or None


def _mpints(blob: bytes) -> list[bytes]:
    """The fields of an OpenSSH public key blob: string, then mpints."""
    fields, at = [], 0
    while at < len(blob):
        (size,) = struct.unpack(">I", blob[at:at + 4])
        fields.append(blob[at + 4:at + 4 + size])
        at += 4 + size
    return fields


def _der_length(size: int) -> bytes:
    if size < 0x80:
        return bytes([size])
    raw = size.to_bytes((size.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def _integer(value: bytes) -> tuple[bytes, bytes]:
    """DER INTEGER header and content (OpenSSH mpints are already minimal two's complement)."""
    return b"\x02" + _der_length(len(value)), value


def key_code(openssh_public: str) -> tuple[str, list[tuple[str, list[str]]]]:
    """`public-key-code` in the form V300 stores a typed RSA peer key: the PKCS#1 sequence header, under it each INTEGER's header, under that its content
    in eight 4-byte words per line. (sequence header, [(integer header, content lines)])."""
    kind, blob = openssh_public.split()[:2]
    if kind != "ssh-rsa":
        raise GeneratorError(f"the image server's host key is {kind}, not ssh-rsa")
    _, e, n = _mpints(base64.b64decode(blob))
    integers = [_integer(n), _integer(e)]
    body = sum(len(h) + len(v) for h, v in integers)
    rows = []
    for header, value in integers:
        words = [value[i:i + 4].hex().upper() for i in range(0, len(value), 4)]
        rows.append((header.hex().upper(),
                     [" ".join(words[i:i + WORDS_PER_LINE]) for i in range(0, len(words), WORDS_PER_LINE)]))
    return (b"\x30" + _der_length(body)).hex().upper(), rows


class SftpServerKey(PartialGenerator):
    """The image server's SSH host key on every switch, so a download from
    it is verified against that key and a switch refuses any other server at that address
    (never `ssh client first-time enable`). The key name is the server's address, as the switch
    itself names a key it accepted.

    Owns exactly the key and the peer line of the current address: a key or peer line for any other
    address (a hand-made peer key) is never touched.
    shortcut: after SFTP_ADDRESS changes, the old address's two lines stay on the switches; remove them
    by hand, or own a list of past addresses (as syslog does) if addresses start to move.
    """

    TAGS = ["software"]

    def acl_huawei(self, device: Any) -> str:
        if not (host := address()):
            return ""
        return f"rsa peer-public-key {host}\n    ~ %global\nssh client peer {host} assign rsa-key {host}"

    def run_huawei(self, device: Any) -> Iterator[str | tuple[str, ...]]:
        if not (host := address()):
            return
        path = Path(os.environ.get("TLS_PUBLIC_DIR") or "/tls-public") / PUBLIC_KEY
        if not path.exists():   # never generate "no key": that would remove the switches' copy
            raise GeneratorError(f"{ADDRESS_ENV} is set but {path} is missing (made at the orchestrator's start)")
        sequence, integers = key_code(path.read_text())
        with self.block("rsa peer-public-key", host):
            yield "public-key-code begin"
            with self.block(sequence):
                for header, content in integers:
                    with self.block(header):
                        yield from content
            yield "public-key-code end"
            yield "peer-public-key end"
        yield ("ssh client peer", host, "assign rsa-key", host)
