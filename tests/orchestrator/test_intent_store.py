"""Intent documents: the platform database is the source, NetBox holds the published copy."""

import asyncio
import json

import httpx
import pytest

from orchestrator import db
from orchestrator.intent_store import IntentStore, metrics
from orchestrator.netbox import NetBoxClient, NetBoxError


def run(coro):
    return asyncio.run(coro)


def fake_contexts():
    """A NetBox with only config contexts, rendered as NetBox does (sites as nested objects)."""
    objects: dict[int, dict] = {}

    def render(obj):
        return {**obj, "sites": [{"id": 1, "slug": "example" if s == 1 else s} for s in obj.get("sites", [])]}

    def handler(request: httpx.Request) -> httpx.Response:
        parts = request.url.path.strip("/").split("/")   # api, extras, config-contexts[, id]
        if parts[1:3] == ["dcim", "sites"]:
            return httpx.Response(200, json={"results": [{"id": 1, "slug": "example"}], "next": None})
        oid = int(parts[3]) if len(parts) > 3 and parts[3] else None
        if request.method == "GET" and oid is None:
            name = request.url.params.get("name")
            found = [render(o) for o in objects.values() if name in (None, o["name"])]
            return httpx.Response(200, json={"results": found, "next": None})
        if request.method == "GET":
            return httpx.Response(200, json=render(objects[oid]))
        if request.method == "POST":
            oid = max(objects, default=0) + 1
            objects[oid] = {"id": oid, **json.loads(request.content)}
            return httpx.Response(201, json=render(objects[oid]))
        if request.method == "PATCH":
            objects[oid].update(json.loads(request.content))
            return httpx.Response(200, json=render(objects[oid]))
        del objects[oid]
        return httpx.Response(204)

    return objects, NetBoxClient("http://netbox", "token", transport=httpx.MockTransport(handler))


CONTEXTS = "extras/config-contexts"


def started(database_url):
    objects, nb = fake_contexts()
    store = IntentStore(db.make_sessionmaker(db.make_engine(database_url, pooled=False)))
    return objects, nb, store


def test_writes_are_versioned_in_the_database_and_published_to_netbox(database_url):
    objects, nb, store = started(database_url)

    async def scenario():
        assert await store.start(nb) == []
        made = await nb.create(CONTEXTS, {"name": "routing-example", "is_active": True, "data": {"a": 1}, "sites": [1]})
        assert made["sites"] == [{"id": 1, "slug": "example"}]
        assert [(o["name"], o["data"], o["sites"]) for o in objects.values()] == [("routing-example", {"a": 1}, [1])]
        read = await nb.find_one(CONTEXTS, name="routing-example")
        await nb.update(CONTEXTS, read["id"], {"data": {**read["data"], "b": 2}})   # as every caller does
        await nb.update(CONTEXTS, read["id"], {"data": {"a": 1, "b": 2}})           # same: no new version
        assert [o["data"] for o in objects.values()] == [{"a": 1, "b": 2}]
        assert await store.compare(nb) == []
        with pytest.raises(NetBoxError, match="already exists"):
            await nb.create(CONTEXTS, {"name": "routing-example", "data": {}, "sites": []})
        await nb.delete(CONTEXTS, read["id"])
        assert objects == {} and await nb.find_one(CONTEXTS, name="routing-example") is None
        again = await nb.create(CONTEXTS, {"name": "routing-example", "data": {"a": 3}, "sites": []})
        assert again["id"] == read["id"]                                           # a document keeps its id
        return await store.history("routing-example")

    history = run(scenario())
    assert [(h["version"], h["data"], h["sites"]) for h in history] == \
        [(1, {"a": 1}, ["example"]), (2, {"a": 1, "b": 2}, ["example"]), (3, None, []), (4, {"a": 3}, [])]


def test_reads_never_use_netboxs_copy(database_url):
    objects, nb, store = started(database_url)

    async def scenario():
        await store.start(nb)
        await nb.create(CONTEXTS, {"name": "fabric-example", "data": {"x": 1}, "sites": [1]})
        await nb.create(CONTEXTS, {"name": "dci", "data": {}, "sites": []})
        next(iter(objects.values()))["data"] = {"x": "changed in NetBox only"}
        assert (await nb.find_one(CONTEXTS, name="fabric-example"))["data"] == {"x": 1}
        assert sorted(c["name"] for c in await nb.find(CONTEXTS)) == ["dci", "fabric-example"]
        with pytest.raises(NetBoxError, match="by name only"):
            await nb.find(CONTEXTS, site="example")
        return await store.compare(nb)

    assert [(d["name"], d["difference"]) for d in run(scenario())] == [("fabric-example", "differs")]


def test_an_install_from_before_the_store_is_adopted_once(database_url):
    objects, nb, store = started(database_url)
    objects[7] = {"id": 7, "name": "fabric-example", "data": {"x": 1}, "sites": [1]}
    run(store.record("fabric-example", {"x": "older"}, ["example"]))               # the shadow had missed a change
    run(store.record("segments-gone", {"y": 1}, ["example"]))                      # ... and a deletion

    async def scenario():
        first = await store.start(nb)
        objects[8] = {"id": 8, "name": "stray", "data": {}, "sites": []}           # written to NetBox by hand later
        objects[7]["data"] = {"x": "edited in NetBox"}
        second = await IntentStore(store.sessions).start(nb)                       # the next start
        return first, second, await nb.find(CONTEXTS)

    first, second, found = run(scenario())
    assert sorted(first) == ["adopted fabric-example from NetBox", "segments-gone is no longer in NetBox: recorded as deleted"]
    assert sorted(second) == ["deleted stray", "updated fabric-example to version 2"]   # NetBox is the copy now
    assert [(c["name"], c["data"]) for c in found] == [("fabric-example", {"x": 1})]
    assert objects[7]["data"] == {"x": 1} and 8 not in objects


def test_a_version_netbox_did_not_take_is_kept_and_published_later(database_url):
    objects, nb, store = started(database_url)

    async def scenario():
        await store.start(nb)
        made = await nb.create(CONTEXTS, {"name": "qos-example", "data": {"q": 1}, "sites": [1]})
        send, calls = nb._send, []

        async def down(method, path, **kwargs):
            calls.append(method)
            raise NetBoxError("cannot reach NetBox")
        nb._send = down
        with pytest.raises(NetBoxError, match="saved in the platform database but was not published"):
            await nb.update(CONTEXTS, made["id"], {"data": {"q": 2}})
        nb._send = send
        assert (await nb.find_one(CONTEXTS, name="qos-example"))["data"] == {"q": 2}     # the source has it
        assert [d["difference"] for d in await store.compare(nb)] == ["differs"]
        assert b"fabric_intent_store_differences 1" in metrics(await store.compare(nb))
        return await store.republish(nb), await store.compare(nb)

    done, after = run(scenario())
    assert done == ["updated qos-example to version 2"] and after == []
    assert [o["data"] for o in objects.values()] == [{"q": 2}]


def test_each_version_records_who_wrote_it(database_url):
    from types import SimpleNamespace

    from orchestrator.deps import current_account
    from orchestrator.intent_store import AUTHOR

    objects, nb, store = started(database_url)

    class Accounts:
        async def user_for(self, session):
            return SimpleNamespace(username="alice", role="operator")

    async def request():   # one request: signed in, then a write
        await current_account(session="cookie", accounts=Accounts())
        await nb.create(CONTEXTS, {"name": "qos-example", "data": {"q": 1}, "sites": [1]})

    async def scenario():
        await store.start(nb)
        await asyncio.create_task(request())        # a request runs in a context of its own
        assert AUTHOR.get() is None                 # nothing leaks out of it
        made = await nb.find_one(CONTEXTS, name="qos-example")
        await nb.update(CONTEXTS, made["id"], {"data": {"q": 2}})   # no request: the platform itself
        return await store.history("qos-example")

    assert [(h["version"], h["written_by"]) for h in run(scenario())] == [(1, "alice"), (2, "platform")]


def test_the_audit_trail_lists_document_versions_by_fabric(database_url):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from orchestrator.app import app, current_account, get_accounts, get_netbox
    from orchestrator.auth import Accounts

    objects, nb, store = started(database_url)
    run(store.start(nb))
    run(store.record("routing-example", {"devices": {}}, ["example"]))
    run(store.record("routing-other", {"devices": {}}, ["other"]))
    accounts = Accounts(store.sessions)
    run(accounts.create_user("bob", "bob-password"))
    saved = app.state._state.get("intent_store")
    app.state.intent_store = store
    app.dependency_overrides[get_accounts] = lambda: accounts
    app.dependency_overrides[get_netbox] = lambda: nb
    try:
        everyone = TestClient(app).get("/audit").json()
        app.dependency_overrides[current_account] = lambda: SimpleNamespace(
            username="carol", role="operator", fabrics={"example": False})
        limited = TestClient(app).get("/audit").json()
    finally:
        app.state.intent_store = saved
        app.dependency_overrides.pop(get_accounts, None)
        app.dependency_overrides.pop(get_netbox, None)

    documents = lambda events: [e["detail"] for e in events if e["action"] == "intent.document"]   # noqa: E731
    assert documents(everyone) == ["routing-other version 1", "routing-example version 1"]
    assert documents(limited) == ["routing-example version 1"]
    assert {e["user"] for e in everyone if e["action"] == "intent.document"} == {"platform"}


def test_a_rollback_point_records_the_document_versions(database_url):
    from orchestrator.intent_snapshots import document_names

    objects, nb, store = started(database_url)

    async def scenario():
        await store.start(nb)
        await nb.create(CONTEXTS, {"name": "routing-example", "data": {"a": 1}, "sites": [1]})
        made = await nb.find_one(CONTEXTS, name="routing-example")
        await nb.update(CONTEXTS, made["id"], {"data": {"a": 2}})
        await nb.create(CONTEXTS, {"name": "overrides-example", "data": {}, "sites": [1]})
        gone = await nb.find_one(CONTEXTS, name="overrides-example")
        await nb.delete(CONTEXTS, gone["id"])
        return await store.versions(document_names("example"))

    assert run(scenario()) == {"routing-example": 2}   # deleted and absent documents left out
