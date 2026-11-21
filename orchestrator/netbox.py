import asyncio
import copy
import json
from dataclasses import dataclass

import httpx

import time

from orchestrator import timing
from orchestrator.timing import measure, request_reads

# Minor versions that Annet's NetBox adapter maps to a storage class
# (annet/adapters/netbox/provider.py). We pin NetBox 4.4 in deploy/compose.yaml.
ANNET_SUPPORTED_MINOR_VERSIONS = frozenset(
    {"3.4", "3.7", "4.0", "4.1", "4.2", "4.3", "4.4", "4.5", "4.6"}
)


# NetBox renders each listed device's merged config context (fabric settings, segments, the
# routing document) unless told not to: seconds per device list (timing log). The
# orchestrator never reads it from a device (generators get it through Annet), so it is left out.
RENDERS_CONTEXT = frozenset({"dcim/devices"})
CONTEXTS = "extras/config-contexts"


WRITES = "writes"   # in a request's read cache: how many writes it has seen
SHARED_SECONDS = 30.0   # how long a read is shared across requests (writes empty it at once)
# a shared read older than this is still answered from memory but read again in the
# background, so the event watcher (every 30 s) keeps the fabric-wide lists warm for the pages.
REFRESH_SECONDS = SHARED_SECONDS / 2


class NetBoxError(Exception):
    pass


@dataclass(frozen=True)
class NetBoxStatus:
    version: str
    rq_workers_running: int

    @property
    def minor_version(self) -> str:
        return ".".join(self.version.split(".")[:2])


class NetBoxClient:
    def __init__(
        self,
        url: str,
        token: str,
        timeout: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._http = httpx.AsyncClient(
            base_url=f"{url.rstrip('/')}/api/",
            headers={"Authorization": f"Token {token}", "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )
        self._shared: dict[tuple, tuple[float, asyncio.Future]] = {}   # see timing.shared
        self._writes = 0
        self._refreshing: set[tuple] = set()
        # The intent store (orchestrator/intent_store.py), once attached: config contexts are then
        # read from and written to the platform database, which publishes them to NetBox. Without
        # it (read-only services, no platform database) they are NetBox's own.
        self.intent = None

    async def status(self) -> NetBoxStatus:
        # /api/status/ requires authentication when LOGIN_REQUIRED is on (the
        # netbox-docker default), so a 200 here also proves the token is valid.
        try:
            response = await self._http.get("status/")
        except httpx.HTTPError as exc:
            raise NetBoxError(f"cannot reach NetBox: {type(exc).__name__}: {exc}".rstrip(": ")) from exc
        if response.status_code in (401, 403):
            raise NetBoxError("NetBox rejected the API token")
        if response.status_code != 200:
            raise NetBoxError(f"NetBox status returned HTTP {response.status_code}")
        try:
            body = response.json()
            return NetBoxStatus(
                version=body["netbox-version"],
                rq_workers_running=body.get("rq-workers-running", 0),
            )
        except (ValueError, KeyError) as exc:
            raise NetBoxError("unexpected response from NetBox status endpoint") from exc

    async def find(self, endpoint: str, **filters) -> list[dict]:
        """GET all objects at `endpoint` (e.g. "dcim/devices") matching `filters`, following pagination."""
        if endpoint == CONTEXTS and self.intent is not None:
            if set(filters) - {"name"}:
                raise NetBoxError(f"intent documents are read by name only, not {sorted(filters)}")
            return await self.intent.find(self, filters.get("name"))
        if timing.shared.get() and (cut := await self._from_lists(endpoint, filters)) is not None:
            return copy.deepcopy(cut)
        return await self._find(endpoint, filters, True)

    async def _find(self, endpoint: str, filters: dict, copied: bool) -> list[dict]:
        """`copied` False: the cached objects themselves, for reading only (copying a
        fabric's whole interface list for each page call cost more than the NetBox read saved)."""
        results: list[dict] = []
        params = {**filters, "limit": 1000, "offset": 0}
        if endpoint in RENDERS_CONTEXT:
            params.setdefault("exclude", "config_context")
        while True:
            body = await self._request("GET", f"{endpoint}/", copied=copied, params=params)
            results.extend(body["results"])
            if not body.get("next"):
                return results
            params["offset"] += params["limit"]

    async def _from_lists(self, endpoint: str, filters: dict) -> list[dict] | None:
        """Shared reads only: a device looked up by name, site or id, and a device's
        interfaces, cut from the device list and the fabric's interface list the event watcher keeps
        warm, instead of one NetBox call per switch after another. None: ask NetBox."""
        if endpoint == "dcim/devices" and filters and set(filters) <= {"name", "site", "id"} and all(
                isinstance(v, (str, int)) for v in filters.values()):
            keys = {"name": lambda d: d["name"], "id": lambda d: d["id"],
                    "site": lambda d: (d.get("site") or {}).get("slug")}
            return [d for d in await self._find("dcim/devices", {}, False)
                    if all(str(keys[k](d)) == str(v) for k, v in filters.items())]
        if endpoint == "dcim/interfaces" and set(filters) == {"device_id"} and isinstance(filters["device_id"], int):
            device = next(iter(await self._from_lists("dcim/devices", {"id": filters["device_id"]})), None)
            site = ((device or {}).get("site") or {}).get("slug")
            if site is None:
                return None
            return [i for i in await self._find("dcim/interfaces", {"site": site}, False)
                    if (i.get("device") or {}).get("id") == filters["device_id"]]
        return None

    async def find_one(self, endpoint: str, **filters) -> dict | None:
        found = await self.find(endpoint, **filters)
        if len(found) > 1:
            raise NetBoxError(f"expected at most one {endpoint} matching {filters}, found {len(found)}")
        return found[0] if found else None

    async def create(self, endpoint: str, data: dict) -> dict:
        if endpoint == CONTEXTS and self.intent is not None:
            return await self.intent.create(self, data)
        return await self._request("POST", f"{endpoint}/", json=data)

    async def update(self, endpoint: str, object_id: int, data: dict) -> dict:
        if endpoint == CONTEXTS and self.intent is not None:
            return await self.intent.update(self, object_id, data)
        return await self._request("PATCH", f"{endpoint}/{object_id}/", json=data)

    async def delete(self, endpoint: str, object_id: int) -> None:
        if endpoint == CONTEXTS and self.intent is not None:
            return await self.intent.delete(self, object_id)
        # A device takes its ports with it: over 5 s on a loaded host (deleting a fabric, 2026-11-20).
        extra = {"timeout": 60.0} if endpoint == "dcim/devices" else {}
        await self._request("DELETE", f"{endpoint}/{object_id}/", **extra)

    async def published_contexts(self) -> list[dict]:
        """The config contexts NetBox itself holds: the published copy of the intent documents."""
        return await self._find(CONTEXTS, {}, True)

    async def allocate_ip(self, prefix_id: int, data: dict) -> dict:
        """Create an IP address from the next available one in the prefix (NetBox picks it)."""
        return await self._request("POST", f"ipam/prefixes/{prefix_id}/available-ips/", json=data)

    async def allocate_prefix(self, prefix_id: int, data: dict) -> dict:
        """Create a child prefix from the next available space in the prefix (NetBox picks it)."""
        return await self._request("POST", f"ipam/prefixes/{prefix_id}/available-prefixes/", json=data)

    async def _request(self, method: str, path: str, copied: bool = True, **kwargs):
        if method != "GET":
            self._writes += 1
            self._shared.clear()   # the platform is NetBox's only writer: nothing else goes stale
        reads = request_reads()
        if reads is None:
            if method == "GET" and timing.shared.get():
                return await self._shared_read(path, copied, **kwargs)
            return await self._send(method, path, **kwargs)
        if method != "GET":
            writes = reads.get(WRITES, 0) + 1
            reads.clear()   # anything read before may have changed
            reads[WRITES] = writes
            return await self._send(method, path, **kwargs)
        key = (path, json.dumps(kwargs.get("params"), sort_keys=True, default=str))
        pending = reads.get(key)
        if pending is None:
            # Shared by every identical read of this request, also those running in parallel.
            writes = reads.get(WRITES, 0)
            # The request's own cache copies for its readers: the shared answer needs no copy of its own.
            read = self._shared_read(path, False, **kwargs) if timing.shared.get() else self._send(method, path, **kwargs)
            pending = reads[key] = asyncio.ensure_future(read)
            try:
                await pending
            finally:
                failed = not pending.done() or pending.cancelled() or pending.exception() is not None
                if reads.get(key) is pending and (failed or reads.get(WRITES, 0) != writes):
                    del reads[key]   # failed, or a write landed meanwhile: do not keep it
        return copy.deepcopy(await pending) if copied else await pending   # callers may change what they get

    async def _shared_read(self, path: str, copied: bool = True, **kwargs):
        """A GET shared across requests and loops for SHARED_SECONDS (identical reads in flight
        share one call); dropped when it fails or a write lands meanwhile."""
        key = (path, json.dumps(kwargs.get("params"), sort_keys=True, default=str))
        now = time.monotonic()
        found = self._shared.get(key)
        if found is not None and REFRESH_SECONDS < now - found[0] <= SHARED_SECONDS and found[1].done() \
                and key not in self._refreshing:
            self._refreshing.add(key)
            asyncio.ensure_future(self._refresh(key, path, kwargs))
        if found is None or now - found[0] > SHARED_SECONDS:
            writes = self._writes
            pending = asyncio.ensure_future(self._send("GET", path, **kwargs))
            self._shared[key] = (now, pending)
            try:
                await pending
            finally:
                failed = not pending.done() or pending.cancelled() or pending.exception() is not None
                if self._shared.get(key, (0, None))[1] is pending:
                    if failed or self._writes != writes:
                        del self._shared[key]
                    else:   # its age counts from when the answer came in, as a refresh's does
                        self._shared[key] = (time.monotonic(), pending)
            found = (now, pending)
        return copy.deepcopy(await found[1]) if copied else await found[1]

    async def _refresh(self, key: tuple, path: str, kwargs: dict) -> None:
        """Read a shared entry again; the old answer is served meanwhile."""
        writes = self._writes
        try:
            body = await self._send("GET", path, **kwargs)
        except NetBoxError:
            return   # the old answer stays until it is too old
        finally:
            self._refreshing.discard(key)
        if self._writes == writes:   # a write meanwhile emptied the cache: do not bring old data back
            done = asyncio.get_running_loop().create_future()
            done.set_result(body)
            self._shared[key] = (time.monotonic(), done)

    async def _send(self, method: str, path: str, **kwargs):
        try:
            with measure("netbox"):
                response = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise NetBoxError(f"cannot reach NetBox: {type(exc).__name__}: {exc}".rstrip(": ")) from exc
        if response.status_code >= 400:
            raise NetBoxError(f"{method} {path} returned HTTP {response.status_code}: {response.text[:500]}")
        return response.json() if response.content else None

    async def aclose(self) -> None:
        await self._http.aclose()
