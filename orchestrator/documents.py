"""Intent documents by name: a fabric's settings (`fabric-<site>`), routing (`routing-<site>`),
overrides (`overrides-<site>`), segments copy (`segments-<site>`) and the DC links (`dci`).

Callers name a document and pass the NetBox they work on. With the platform's NetBox client the
intent store (orchestrator/intent_store.py) answers: the platform database is the source, NetBox
holds the published copy the generators read. The drafts' copy and the in-memory NetBox
(orchestrator/memory_netbox.py) hold them as config contexts, the form these functions use.
"""

ENDPOINT = "extras/config-contexts"


async def find(nb, name: str) -> dict | None:
    """The document as {"id", "name", "data", "sites"}, or None."""
    return await nb.find_one(ENDPOINT, name=name)


async def every(nb) -> list[dict]:
    return await nb.find(ENDPOINT)


async def create(nb, name: str, data: dict, sites: list | None = None) -> dict:
    """A new document (refused by the store if the name exists); `sites` are NetBox site ids."""
    return await nb.create(ENDPOINT, {"name": name, "data": data, "sites": sites or []})


async def save(nb, document: dict, data: dict, sites: list | None = None) -> dict:
    """A new version of a document found earlier; its sites kept unless given."""
    return await nb.update(ENDPOINT, document["id"], {"data": data} | ({} if sites is None else {"sites": sites}))


async def put(nb, name: str, data: dict, sites: list | None = None) -> dict:
    """Create the document or save a new version of it."""
    found = await find(nb, name)
    return await save(nb, found, data, sites) if found else await create(nb, name, data, sites)


async def remove(nb, document: dict) -> None:
    await nb.delete(ENDPOINT, document["id"])
