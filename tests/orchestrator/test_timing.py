"""Request timing (orchestrator/timing.py): NetBox calls made inside a request are tallied."""

import asyncio

import httpx

from orchestrator import timing
from orchestrator.netbox import NetBoxClient


def test_netbox_calls_are_tallied_only_inside_a_request():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [], "next": None})

    async def scenario() -> timing.Tally:
        nb = NetBoxClient("http://netbox", "t", transport=httpx.MockTransport(handler))
        await nb.find("dcim/devices")              # no request: not counted, no error
        tally = timing.Tally()
        token = timing.current.set(tally)
        try:
            await nb.find("dcim/devices")
            await nb.find("dcim/interfaces")
        finally:
            timing.current.reset(token)
        await nb.aclose()
        return tally

    tally = asyncio.run(scenario())
    assert tally.calls == {"netbox": 2}
    assert tally.summary().startswith("netbox=2/")
    assert tally.header(0.5).endswith("total;dur=500.0") and 'desc="2 calls"' in tally.header(0.5)


def test_device_lists_leave_out_the_config_context():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.url.params.get("exclude")))
        return httpx.Response(200, json={"results": [], "next": None})

    async def scenario():
        nb = NetBoxClient("http://netbox", "t", transport=httpx.MockTransport(handler))
        await nb.find("dcim/devices", site="example")
        await nb.find_one("dcim/devices", name="leaf1")
        await nb.find("dcim/interfaces", device_id=1)
        await nb.aclose()

    asyncio.run(scenario())
    assert seen == [("/api/dcim/devices/", "config_context"), ("/api/dcim/devices/", "config_context"),
                    ("/api/dcim/interfaces/", None)]


def test_a_request_reads_the_same_thing_once_until_it_writes():
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.method)
        return httpx.Response(200, json={"results": [{"id": 1, "name": "leaf1"}], "next": None})

    async def scenario():
        nb = NetBoxClient("http://netbox", "t", transport=httpx.MockTransport(handler))
        tally = timing.Tally()
        token = timing.current.set(tally)
        try:
            first = await nb.find_one("dcim/devices", name="leaf1")
            first["name"] = "changed by the caller"
            again = await nb.find_one("dcim/devices", name="leaf1")     # cached, unchanged
            await nb.update("dcim/devices", 1, {"status": "active"})
            await nb.find_one("dcim/devices", name="leaf1")             # read again after a write
            tally.open = False                                               # the request ended
            await nb.find_one("dcim/devices", name="leaf1")             # a task it started: no cache
        finally:
            timing.current.reset(token)
        await nb.aclose()
        return again, tally

    again, tally = asyncio.run(scenario())
    assert again["name"] == "leaf1"
    assert sent == ["GET", "PATCH", "GET", "GET"]
    assert tally.calls == {"netbox": 3}   # the last one came after the request ended


def test_parallel_identical_reads_share_one_call():
    sent = []

    async def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.method)
        await asyncio.sleep(0.01)
        return httpx.Response(200, json={"results": [{"id": 1}], "next": None})

    async def scenario():
        nb = NetBoxClient("http://netbox", "t", transport=httpx.MockTransport(handler))
        token = timing.current.set(timing.Tally())
        try:
            found = await asyncio.gather(*(nb.find("dcim/sites", slug="example") for _ in range(3)))
        finally:
            timing.current.reset(token)
        await nb.aclose()
        return found

    found = asyncio.run(scenario())
    assert sent == ["GET"] and found == [[{"id": 1}]] * 3
    assert found[0] is not found[1]   # each caller gets its own copy


def test_a_read_overtaken_by_a_write_is_not_kept():
    sent = []

    async def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.method)
        if request.method == "GET":
            await asyncio.sleep(0.02)   # the PATCH below lands while this read is under way
        return httpx.Response(200, json={"results": [], "next": None})

    async def scenario():
        nb = NetBoxClient("http://netbox", "t", transport=httpx.MockTransport(handler))
        token = timing.current.set(timing.Tally())
        try:
            for _ in range(2):   # the second round checks the write counter survives the cache reset
                await asyncio.gather(nb.find("dcim/devices", name="x"), nb.update("dcim/devices", 1, {}))
            await nb.find("dcim/devices", name="x")   # not served from the overtaken read
        finally:
            timing.current.reset(token)
        await nb.aclose()

    asyncio.run(scenario())
    assert sent.count("GET") == 3


def test_shared_reads_span_requests_until_a_write_or_their_age(monkeypatch):
    """GET requests and the read-only loops share NetBox reads for SHARED_SECONDS; any write
    empties that cache, and reads without the flag (writes, jobs) always go to NetBox."""
    from orchestrator import netbox as netbox_module

    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append((request.method, request.url.path))
        return httpx.Response(200, json={"results": [{"id": len(sent)}], "next": None})

    clock = [1000.0]
    monkeypatch.setattr(netbox_module.time, "monotonic", lambda: clock[0])

    async def in_request(nb, shared: bool, call):
        tally, flag = timing.Tally(), timing.shared.set(shared)
        token = timing.current.set(tally)
        try:
            return await call(nb)
        finally:
            tally.open = False
            timing.current.reset(token)
            timing.shared.reset(flag)

    async def scenario():
        nb = NetBoxClient("http://netbox", "t", transport=httpx.MockTransport(handler))
        devices = lambda nb: nb.find("dcim/devices")   # noqa: E731
        first = await in_request(nb, True, devices)
        again = await in_request(nb, True, devices)         # another request: shared
        assert again == first and len(sent) == 1
        await in_request(nb, False, devices)                # a write request reads NetBox itself
        assert len(sent) == 2
        timing.shared.set(True)                             # a background loop, outside any request
        assert await nb.find("dcim/devices") == first and len(sent) == 2
        await in_request(nb, False, lambda nb: nb.update("dcim/devices", 1, {"name": "x"}))
        await in_request(nb, True, devices)                 # the write emptied it
        assert len(sent) == 4
        clock[0] += netbox_module.SHARED_SECONDS + 1
        await in_request(nb, True, devices)                 # too old
        assert len(sent) == 5
        await nb.aclose()

    asyncio.run(scenario())


def test_shared_reads_refresh_ahead_and_cut_device_lookups_from_the_lists(monkeypatch):
    """A shared read past REFRESH_SECONDS is answered at once and read again behind;
    a device by name and its interfaces come from the device list and the fabric's interface list."""
    from orchestrator import netbox as netbox_module

    sent = []
    devices = [{"id": 1, "name": "leaf1", "site": {"slug": "f"}}, {"id": 2, "name": "leaf2", "site": {"slug": "f"}}]
    interfaces = [{"id": 10, "name": "10GE1/0/1", "device": {"id": 1}}, {"id": 20, "name": "10GE1/0/1", "device": {"id": 2}}]

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append((request.url.path, dict(request.url.params)))
        body = devices if request.url.path.endswith("devices/") else interfaces
        return httpx.Response(200, json={"results": body, "next": None})

    clock = [1000.0]
    monkeypatch.setattr(netbox_module.time, "monotonic", lambda: clock[0])

    async def scenario():
        timing.shared.set(True)
        nb = NetBoxClient("http://netbox", "t", transport=httpx.MockTransport(handler))
        assert [d["id"] for d in await nb.find("dcim/devices", name="leaf2")] == [2]
        assert await nb.find_one("dcim/devices", site="f", name="nope") is None
        assert [i["id"] for i in await nb.find("dcim/interfaces", device_id=1)] == [10]
        assert [p for p, _ in sent] == ["/api/dcim/devices/", "/api/dcim/interfaces/"]   # two lists, nothing per switch
        assert sent[1][1]["site"] == "f"
        clock[0] += netbox_module.REFRESH_SECONDS + 1
        await nb.find("dcim/devices")            # answered from memory, read again behind
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(sent) == 3
        clock[0] += netbox_module.SHARED_SECONDS - 1   # the first read is too old, the refresh is not
        await nb.find("dcim/devices")            # from memory again, refreshed again behind
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert [p for p, _ in sent[2:]] == ["/api/dcim/devices/"] * 2
        timing.shared.set(False)                 # writes and jobs: NetBox itself, as asked
        await nb.find("dcim/interfaces", device_id=1)
        assert sent[-1][1].get("device_id") == "1"
        await nb.aclose()

    asyncio.run(scenario())


def test_a_device_delete_waits_longer_than_other_calls():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.path] = request.extensions["timeout"]["read"]
        return httpx.Response(204)

    async def scenario() -> None:
        nb = NetBoxClient("http://netbox", "t", transport=httpx.MockTransport(handler))
        await nb.delete("dcim/devices", 1)
        await nb.delete("dcim/cables", 2)

    asyncio.run(scenario())
    assert seen == {"/api/dcim/devices/1/": 60.0, "/api/dcim/cables/2/": 5.0}
