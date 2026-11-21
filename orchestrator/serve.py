"""Run the orchestrator over HTTPS only: make or reuse the self-signed certificate, then
start uvicorn with it. The image's default command.

    python -m orchestrator.serve
"""

import os
import sys

import uvicorn

from orchestrator import tls


def _gui_settings() -> None:
    """The GUI's settings before the certificate is made: TLS_HOSTNAMES may be one of them.
    Best effort: on a database not migrated yet, the .env values are used."""
    if not os.environ.get("DATABASE_URL"):
        return
    import asyncio

    from orchestrator import db
    from orchestrator.platform_settings import PlatformSettings

    async def load() -> None:
        engine = db.make_engine(os.environ["DATABASE_URL"], pooled=False)
        try:
            await PlatformSettings(db.make_sessionmaker(engine)).load()
        finally:
            await engine.dispose()

    try:
        asyncio.run(load())
    except Exception as exc:   # first start of a new version: the table comes with the migration
        print(f"settings: using .env values ({type(exc).__name__})", file=sys.stderr, flush=True)


def main() -> int:
    _gui_settings()
    hostnames = tls.hostnames_from_env()
    cert, key, fp, made = tls.ensure(hostnames=hostnames)
    tls.publish(cert)
    print(tls.describe(fp, made, hostnames), file=sys.stderr, flush=True)
    from orchestrator import sftp   # the image server's host key, written to every switch
    sftp_fp, sftp_made = sftp.ensure_host_key()
    print(f"SFTP: {'made a new' if sftp_made else 'using the'} host key {sftp_fp}", file=sys.stderr, flush=True)
    uvicorn.run("orchestrator.app:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")),
                ssl_certfile=str(cert), ssl_keyfile=str(key),
                # TLS 1.2 or later (Python's default); on 1.2 only forward-secret AEAD suites
                ssl_ciphers="ECDHE+AESGCM:ECDHE+CHACHA20")
    return 0


if __name__ == "__main__":
    sys.exit(main())
