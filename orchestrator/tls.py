"""The orchestrator's self-signed TLS certificate (self-signed, in the lab
and in production). The orchestrator serves HTTPS only.

At start-up the certificate in TLS_DIR (a Compose volume, never the repository) is reused
unless it is missing, expires within RENEW_BEFORE, or does not name every host in
TLS_HOSTNAMES; then a new one is made. Its SHA256 fingerprint is printed, so a person can
compare it with what the browser shows before accepting it. The certificate alone (no key)
is also copied to TLS_PUBLIC_DIR, which Prometheus mounts to trust exactly this certificate.

    python -m orchestrator.tls     # make or check the certificate, print its fingerprint
"""

import datetime
import ipaddress
import os
import sys
from pathlib import Path

DEFAULT_DIR = "/tls"
DEFAULT_PUBLIC_DIR = "/tls-public"
DEFAULT_HOSTNAMES = "localhost,127.0.0.1,orchestrator"   # orchestrator: Prometheus, inside Compose
CERT, KEY = "fabric.crt", "fabric.key"
VALIDITY = datetime.timedelta(days=825)
RENEW_BEFORE = datetime.timedelta(days=30)


def hostnames_from_env() -> list[str]:
    names = os.environ.get("TLS_HOSTNAMES") or DEFAULT_HOSTNAMES
    return [n.strip() for n in names.split(",") if n.strip()]


def paths(directory: str | Path | None = None) -> tuple[Path, Path]:
    base = Path(directory or os.environ.get("TLS_DIR") or DEFAULT_DIR)
    return base / CERT, base / KEY


def _names(hostnames: list[str]):
    from cryptography import x509

    names = []
    for host in hostnames:
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            names.append(x509.DNSName(host))
    return names


def _covers(cert, hostnames: list[str]) -> bool:
    from cryptography import x509

    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return False
    return set(_names(hostnames)) <= set(san)


def _expires(cert) -> datetime.datetime:
    after = getattr(cert, "not_valid_after_utc", None)   # cryptography >= 42
    return after or cert.not_valid_after.replace(tzinfo=datetime.timezone.utc)


def fingerprint(cert) -> str:
    from cryptography.hazmat.primitives import hashes

    return ":".join(f"{b:02X}" for b in cert.fingerprint(hashes.SHA256()))


def generate(hostnames: list[str], now: datetime.datetime | None = None):
    """A new key and self-signed certificate for these hostnames (ECDSA P-256)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = now or datetime.datetime.now(datetime.timezone.utc)
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostnames[0]),
                         x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Diffplane (self-signed)")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + VALIDITY)
        .add_extension(x509.SubjectAlternativeName(_names(hostnames)), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def ensure(directory: str | Path | None = None, hostnames: list[str] | None = None,
           now: datetime.datetime | None = None) -> tuple[Path, Path, str, bool]:
    """(certificate path, key path, SHA256 fingerprint, whether a new one was made)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    hostnames = hostnames or hostnames_from_env()
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cert_path, key_path = paths(directory)
    if cert_path.exists() and key_path.exists():
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        if _expires(cert) - now > RENEW_BEFORE and _covers(cert, hostnames):
            return cert_path, key_path, fingerprint(cert), False

    key, cert = generate(hostnames, now)
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_bytes = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption())
    tmp = key_path.with_name(key_path.name + ".tmp")
    tmp.touch(mode=0o600)
    tmp.write_bytes(key_bytes)
    os.replace(tmp, key_path)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path, fingerprint(cert), True


def publish(cert_path: Path, directory: str | Path | None = None) -> Path:
    """Copy the certificate (never the key) to the directory Prometheus reads."""
    target = Path(directory or os.environ.get("TLS_PUBLIC_DIR") or DEFAULT_PUBLIC_DIR) / CERT
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(cert_path.read_bytes())
    os.replace(tmp, target)
    return target


def describe(fp: str, made: bool, hostnames: list[str]) -> str:
    action = "made a new" if made else "using the"
    return (f"TLS: {action} self-signed certificate for {', '.join(hostnames)}\n"
            f"TLS: SHA256 fingerprint {fp}\n"
            "TLS: compare it with the certificate your browser shows before accepting it")


def main() -> int:
    hostnames = hostnames_from_env()
    _, _, fp, made = ensure(hostnames=hostnames)
    print(describe(fp, made, hostnames))
    return 0


if __name__ == "__main__":
    sys.exit(main())
