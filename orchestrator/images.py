"""Switch software images: uploaded once in the GUI by an admin, kept in
the image store (IMAGES_DIR, the volume `sftpd` serves read-only), listed with what the platform
computed: kind, release, size and SHA-256. A SHA-256 typed from Huawei's download page is optional;
when typed it must match, or the upload is refused and nothing is kept.

The release comes from Huawei's file name (`..._V300R025C00SPC500.cc`, `..._V300R025SPH120.PAT`): the
upgrade later checks the switch reports exactly that release.
"""

import hashlib
import os
import re
import secrets
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from orchestrator import db
from orchestrator.auth import audit

# Flash file names: letters, digits, dot, dash, underscore (Huawei's own names), never a path.
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
SYSTEM = re.compile(r"_(V\d{3}R\d{3}C\d{2}SPC\d{3})\.cc", re.I)
PATCH = re.compile(r"_(V\d{3}R\d{3}(?:C\d{2})?SPH\d{3})\.PAT", re.I)
SHA256 = re.compile(r"[0-9a-fA-F]{64}")
CHUNK = 1 << 20


class ImageError(ValueError):
    pass


def kind_and_release(name: str) -> tuple[str, str]:
    if not NAME.fullmatch(name):
        raise ImageError("a file name of letters, digits, '.', '-' and '_' only (Huawei's own name)")
    if match := SYSTEM.search(name):
        if match.end() == len(name):
            return "system", match.group(1).upper()
    if match := PATCH.search(name):
        if match.end() == len(name):
            return "patch", match.group(1).upper()
    raise ImageError("Huawei's file name is needed: system software ..._V300R025C00SPC500.cc "
                     "or a patch ..._V300R025SPH120.PAT")


def view(image: db.SoftwareImage, present: bool) -> dict:
    return {"id": image.id, "present": present, "name": image.name, "kind": image.kind, "release": image.release, "size": image.size,
            "sha256": image.sha256, "sha256_typed": image.sha256_typed, "uploaded_by": image.uploaded_by,
            "uploaded_at": image.uploaded_at.isoformat()}


class ImageStore:
    def __init__(self, sessions: async_sessionmaker, directory: str | Path | None = None):
        self.sessions = sessions
        self.directory = Path(directory or os.environ.get("IMAGES_DIR") or "/images")

    async def list(self) -> list[dict]:
        async with self.sessions() as session:
            found = (await session.scalars(select(db.SoftwareImage).order_by(db.SoftwareImage.release.desc(),
                                                                              db.SoftwareImage.name))).all()
        # in HA each server has its own store: re-uploaded after a failover if needed
        return [view(i, (self.directory / i.name).is_file()) for i in found]

    async def get(self, image_id: int) -> db.SoftwareImage | None:
        async with self.sessions() as session:
            return await session.get(db.SoftwareImage, image_id)

    def path(self, image: db.SoftwareImage) -> Path:
        return self.directory / image.name

    async def add(self, name: str, chunks: AsyncIterator[bytes], typed: str | None, user: str) -> dict:
        kind, release = kind_and_release(name)
        typed = (typed or "").strip().lower() or None
        if typed and not SHA256.fullmatch(typed):
            raise ImageError("the typed SHA-256 must be 64 hexadecimal digits")
        async with self.sessions() as session:
            if await session.scalar(select(db.SoftwareImage).where(db.SoftwareImage.name == name)):
                raise ImageError(f"{name} is already in the store")
        target = self.directory / name
        partial = self.directory / f".upload-{secrets.token_hex(8)}"
        digest, size = hashlib.sha256(), 0
        try:
            with open(partial, "xb") as out:
                async for chunk in chunks:
                    digest.update(chunk)
                    size += len(chunk)
                    out.write(chunk)
            computed = digest.hexdigest()
            if size == 0:
                raise ImageError("the file is empty")
            if typed and typed != computed:
                raise ImageError(f"the file's SHA-256 is {computed}, not the typed {typed}: "
                                 "the download may be damaged; nothing was kept")
            if target.exists():
                raise ImageError(f"{name} is already in the store")
            os.replace(partial, target)
        finally:
            partial.unlink(missing_ok=True)
        image = db.SoftwareImage(name=name, kind=kind, release=release, size=size, sha256=computed,
                                 sha256_typed=bool(typed), uploaded_by=user)
        async with self.sessions() as session:
            session.add(image)
            await session.commit()
            await session.refresh(image)
        await audit(self.sessions, user, "image.uploaded", detail=f"{name} sha256 {computed}")
        return view(image, True)

    async def delete(self, image_id: int, user: str) -> None:
        async with self.sessions() as session:
            image = await session.get(db.SoftwareImage, image_id)
            if image is None:
                raise LookupError(f"no image {image_id}")
            (self.directory / image.name).unlink(missing_ok=True)
            await session.delete(image)
            await session.commit()
        await audit(self.sessions, user, "image.deleted", detail=image.name)
