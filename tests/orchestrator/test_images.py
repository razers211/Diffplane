"""The switch software image store: admins upload, the SHA-256 is computed, a typed one must match."""

import asyncio
import hashlib

import pytest
from fastapi.testclient import TestClient

from orchestrator import db
from orchestrator.app import app, current_account, current_user, get_accounts, get_images
from orchestrator.auth import Accounts
from orchestrator.images import ImageError, ImageStore, kind_and_release

SYSTEM, PATCH = "CE6800-8800-9800_V300R025C00SPC500.cc", "CE6800-8800-9800_V300R025SPH120.PAT"
BODY = b"image" * 100000


def test_kind_and_release_from_huaweis_names():
    assert kind_and_release(SYSTEM) == ("system", "V300R025C00SPC500")
    assert kind_and_release(PATCH) == ("patch", "V300R025SPH120")
    assert kind_and_release("CE6800_V300R024C00SPC500.CC") == ("system", "V300R024C00SPC500")
    for bad in ("../x_V300R025C00SPC500.cc", "x.cc", "CE6800_V300R025C00SPC500.cc.bak", "a b_V300R025SPH120.PAT", ""):
        with pytest.raises(ImageError):
            kind_and_release(bad)


async def chunks(data: bytes):
    for i in range(0, len(data), 65536):
        yield data[i:i + 65536]


@pytest.fixture
def store(database_url, tmp_path):
    return ImageStore(db.make_sessionmaker(db.make_engine(database_url, pooled=False)), tmp_path)


def test_upload_keeps_the_file_with_its_digest(store, tmp_path):
    image = asyncio.run(store.add(SYSTEM, chunks(BODY), None, "root"))
    assert image["sha256"] == hashlib.sha256(BODY).hexdigest() and image["size"] == len(BODY)
    assert image["kind"] == "system" and image["release"] == "V300R025C00SPC500" and not image["sha256_typed"]
    assert (tmp_path / SYSTEM).read_bytes() == BODY and [p.name for p in tmp_path.iterdir()] == [SYSTEM]
    assert [i["name"] for i in asyncio.run(store.list())] == [SYSTEM] and asyncio.run(store.list())[0]["present"]
    with pytest.raises(ImageError, match="already"):
        asyncio.run(store.add(SYSTEM, chunks(BODY), None, "root"))


def test_a_typed_digest_must_match_and_nothing_is_kept_otherwise(store, tmp_path):
    with pytest.raises(ImageError, match="not the typed"):
        asyncio.run(store.add(PATCH, chunks(BODY), "0" * 64, "root"))
    assert list(tmp_path.iterdir()) == [] and asyncio.run(store.list()) == []
    image = asyncio.run(store.add(PATCH, chunks(BODY), hashlib.sha256(BODY).hexdigest().upper(), "root"))
    assert image["sha256_typed"]


def test_delete_removes_file_and_row(store, tmp_path):
    image = asyncio.run(store.add(PATCH, chunks(BODY), None, "root"))
    asyncio.run(store.delete(image["id"], "root"))
    assert list(tmp_path.iterdir()) == [] and asyncio.run(store.list()) == []


def test_only_admins_upload_and_delete(store, database_url):
    accounts = Accounts(db.make_sessionmaker(db.make_engine(database_url, pooled=False)))
    asyncio.run(accounts.create_user("root", "root-password", "admin"))
    asyncio.run(accounts.create_user("bob", "bob-password", "approver"))
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(current_account, None)
    app.dependency_overrides.update({get_accounts: lambda: accounts, get_images: lambda: store})
    try:
        client = TestClient(app, raise_server_exceptions=False, base_url="https://testserver")
        client.post("/auth/login", json={"username": "bob", "password": "bob-password"})
        assert client.post("/images", params={"name": SYSTEM}, content=BODY).status_code == 403
        client.post("/auth/login", json={"username": "root", "password": "root-password"})
        assert client.post("/images", params={"name": "x.cc"}, content=BODY).status_code == 400
        made = client.post("/images", params={"name": SYSTEM}, content=BODY,
                           headers={"content-type": "application/octet-stream"})
        assert made.status_code == 201 and made.json()["sha256"] == hashlib.sha256(BODY).hexdigest()
        client.post("/auth/login", json={"username": "bob", "password": "bob-password"})
        assert [i["name"] for i in client.get("/images").json()] == [SYSTEM]   # everyone sees the store
        assert client.delete(f"/images/{made.json()['id']}").status_code == 403
        client.post("/auth/login", json={"username": "root", "password": "root-password"})
        assert client.delete(f"/images/{made.json()['id']}").status_code == 204
    finally:
        app.dependency_overrides.pop(get_accounts, None)
        app.dependency_overrides.pop(get_images, None)
