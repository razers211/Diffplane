"""The orchestrator's self-signed certificate: made once, reused, renewed in time."""

import datetime
import ipaddress
import stat

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from orchestrator import tls

NAMES = ["localhost", "127.0.0.1", "orchestrator"]
NOW = datetime.datetime(2026, 10, 7, tzinfo=datetime.timezone.utc)


def load(path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(path.read_bytes())


def test_new_certificate_names_every_host_and_keeps_the_key_private(tmp_path):
    cert_path, key_path, fp, made = tls.ensure(tmp_path, NAMES, NOW)

    cert = load(cert_path)
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert made and set(san.get_values_for_type(x509.DNSName)) == {"localhost", "orchestrator"}
    assert san.get_values_for_type(x509.IPAddress) == [ipaddress.ip_address("127.0.0.1")]
    assert cert.issuer == cert.subject   # self-signed
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    assert key.public_key().public_numbers() == cert.public_key().public_numbers()
    assert fp == tls.fingerprint(cert) and len(fp.split(":")) == 32


def test_a_valid_certificate_is_reused(tmp_path):
    _, _, first, _ = tls.ensure(tmp_path, NAMES, NOW)
    _, _, again, made = tls.ensure(tmp_path, NAMES, NOW + datetime.timedelta(days=700))

    assert not made and again == first


def test_renewed_shortly_before_it_expires(tmp_path):
    _, _, first, _ = tls.ensure(tmp_path, NAMES, NOW)
    _, _, renewed, made = tls.ensure(tmp_path, NAMES, NOW + tls.VALIDITY - datetime.timedelta(days=10))

    assert made and renewed != first


def test_renewed_when_a_hostname_is_added(tmp_path):
    tls.ensure(tmp_path, NAMES, NOW)
    cert_path, _, _, made = tls.ensure(tmp_path, NAMES + ["fabric.example.net"], NOW)

    san = load(cert_path).extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert made and "fabric.example.net" in san.get_values_for_type(x509.DNSName)


def test_only_the_certificate_is_published(tmp_path):
    cert_path, _, _, _ = tls.ensure(tmp_path / "private", NAMES, NOW)

    published = tls.publish(cert_path, tmp_path / "public")

    assert published.read_bytes() == cert_path.read_bytes()
    assert [p.name for p in (tmp_path / "public").iterdir()] == ["fabric.crt"]
