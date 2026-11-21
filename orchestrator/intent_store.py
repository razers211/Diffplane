"""Intent documents: the platform database is their source (ROADMAP, code redesign step 2).

A document is what used to live only in a NetBox config context: a fabric's settings
(`fabric-<site>`), its segments copy, routing intent, overrides, DC links. Every version is kept in
the table `intent_documents`. Callers name a document through `orchestrator/documents.py` and pass
the NetBox they work on; once this store is attached (`NetBoxClient.intent`) the client hands those
calls here:

- reads come from the newest versions here, never from NetBox;
- a write records a new version here first, then publishes it to NetBox, whose config contexts are
  the copy the generators read through Annet. A version that could not be published is an error
  naming `republish`; the orchestrator also republishes at every start.

`compare` lists where NetBox's copy differs (exported on /metrics, alert FabricIntentStoreDiffers).
The drafts' in-memory NetBox is a copy of these documents and never writes here: a staged change
is recorded when it is applied at its window.

An install from before this store is adopted once, at the first start: NetBox's contexts, the
source until then, become the newest versions (marked by the settings row INTENT_SOURCE).

    python -m orchestrator.intent_store check      # where NetBox's copy differs; exit 1 if it does
    python -m orchestrator.intent_store republish  # NetBox's copy made equal to the database
"""

import asyncio
import sys
from contextvars import ContextVar

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from orchestrator import db
from orchestrator.db import IntentDocument, Setting
from orchestrator.netbox import NetBoxError

CONTEXTS = "extras/config-contexts"
METRIC = "fabric_intent_store_differences"
MARK = "INTENT_SOURCE"   # settings row, value "database": NetBox's contexts were adopted
PUBLISHED = "Published copy of an intent document in the platform database; do not edit"

# The account a request acts as (set by orchestrator/deps.py current_account), recorded with each
# version it writes; jobs it starts inherit it. Unset: the platform itself (start, seed, schedulers).
AUTHOR: ContextVar[str | None] = ContextVar("intent_author", default=None)
PLATFORM = "platform"


def _sites(context: dict) -> list[str]:
    return sorted(s["slug"] if isinstance(s, dict) else str(s) for s in context.get("sites") or [])


class IntentStore:
    def __init__(self, sessions):
        self.sessions = sessions
        self._site_ids: dict[str, int] = {}   # NetBox's sites: a document is assigned to site slugs

    # -- the versions ------------------------------------------------------------

    async def latest(self) -> dict[str, IntentDocument]:
        """The newest version of each document, deleted ones included."""
        newest = select(IntentDocument.name, func.max(IntentDocument.version).label("v")) \
            .group_by(IntentDocument.name).subquery()
        async with self.sessions() as session:
            rows = await session.scalars(select(IntentDocument).join(
                newest, (IntentDocument.name == newest.c.name) & (IntentDocument.version == newest.c.v)))
            return {row.name: row for row in rows}

    async def record(self, name: str, data: dict | None, sites: list[str]) -> int | None:
        """A new version when it differs from the newest one (None: unchanged)."""
        for _ in range(3):   # two writers of one document at once: the second takes the next number
            async with self.sessions() as session:
                last = await session.scalar(select(IntentDocument).where(IntentDocument.name == name)
                                            .order_by(IntentDocument.version.desc()).limit(1))
                if last is not None and last.data == data and (data is None or last.sites == sites):
                    return None
                if last is None and data is None:
                    return None
                version = (last.version if last else 0) + 1
                session.add(IntentDocument(name=name, version=version, data=data, sites=sites,
                                           written_by=AUTHOR.get() or PLATFORM))
                try:
                    await session.commit()
                    return version
                except IntegrityError:
                    await session.rollback()
        raise RuntimeError(f"intent document {name}: no free version number after 3 tries")

    async def history(self, name: str) -> list[dict]:
        async with self.sessions() as session:
            rows = await session.scalars(select(IntentDocument).where(IntentDocument.name == name)
                                         .order_by(IntentDocument.version))
            return [{"version": r.version, "written_at": r.written_at.isoformat(), "written_by": r.written_by,
                     "sites": r.sites, "data": r.data} for r in rows]

    async def newest(self, limit: int) -> list[IntentDocument]:
        """The most recently written versions of every document, newest first (the audit trail)."""
        async with self.sessions() as session:
            return list(await session.scalars(select(IntentDocument).order_by(IntentDocument.id.desc()).limit(limit)))

    async def versions(self, names: list[str]) -> dict[str, int]:
        """The newest version number of each named document that exists (a rollback point's)."""
        rows = await self.latest()
        return {name: rows[name].version for name in sorted(names) if name in rows and rows[name].data is not None}

    # -- what the NetBox client hands over (config contexts, by name) -------------

    async def _ids(self) -> dict[str, int]:
        """A document's id for callers: the row id of its first version, which never changes."""
        async with self.sessions() as session:
            return {name: first for name, first in (await session.execute(
                select(IntentDocument.name, func.min(IntentDocument.id)).group_by(IntentDocument.name))).all()}

    async def _site_map(self, nb, wanted=()) -> dict[str, int]:
        if not self._site_ids or set(wanted) - set(self._site_ids) - set(self._site_ids.values()):
            self._site_ids = {site["slug"]: site["id"] for site in await nb.find("dcim/sites")}
        return self._site_ids

    async def _render(self, nb, row: IntentDocument, doc_id: int) -> dict:
        ids = await self._site_map(nb, row.sites)
        return {"id": doc_id, "name": row.name, "data": row.data,
                "sites": [{"id": ids.get(slug), "slug": slug} for slug in row.sites]}

    async def find(self, nb, name: str | None = None) -> list[dict]:
        rows, ids = await asyncio.gather(self.latest(), self._ids())
        return [await self._render(nb, row, ids[row.name]) for row in rows.values()
                if row.data is not None and name in (None, row.name)]

    async def _slugs(self, nb, sites: list) -> list[str]:
        """A write names sites as NetBox does, by id."""
        ids = await self._site_map(nb, sites)
        by_id = {site_id: slug for slug, site_id in ids.items()}
        if unknown := [s for s in sites if s not in by_id and s not in ids]:
            raise NetBoxError(f"intent document: no site {unknown}")
        return sorted(by_id.get(s, s) for s in sites)

    async def _row(self, doc_id: int) -> IntentDocument:
        name = next((n for n, i in (await self._ids()).items() if i == doc_id), None)
        row = (await self.latest()).get(name)
        if row is None or row.data is None:
            raise NetBoxError(f"no intent document with id {doc_id}")
        return row

    async def create(self, nb, payload: dict) -> dict:
        name = payload["name"]
        if (existing := (await self.latest()).get(name)) is not None and existing.data is not None:
            raise NetBoxError(f"intent document {name} already exists")
        await self.record(name, payload.get("data") or {}, await self._slugs(nb, payload.get("sites") or []))
        await self._publish(nb, name)
        return (await self.find(nb, name))[0]

    async def update(self, nb, doc_id: int, payload: dict) -> dict:
        row = await self._row(doc_id)
        sites = await self._slugs(nb, payload["sites"]) if "sites" in payload else row.sites
        await self.record(row.name, payload.get("data", row.data), sites)
        await self._publish(nb, row.name)   # also when nothing changed: an unpublished version goes out
        return (await self.find(nb, row.name))[0]

    async def delete(self, nb, doc_id: int) -> None:
        row = await self._row(doc_id)
        await self.record(row.name, None, [])
        await self._publish(nb, row.name)

    # -- NetBox's copy -----------------------------------------------------------

    async def _put(self, nb, context: dict | None, row: IntentDocument | None) -> str | None:
        """One config context in NetBox made equal to its document: what was done, if anything.
        Straight to NetBox (`_request`), never back through this store."""
        live = row is not None and row.data is not None
        if context is None and not live:
            return None
        if not live:
            await nb._request("DELETE", f"{CONTEXTS}/{context['id']}/")
            return f"deleted {context['name']}"
        ids = await self._site_map(nb, row.sites)
        if gone := [slug for slug in row.sites if slug not in ids]:
            raise NetBoxError(f"{row.name} is assigned to {gone}, not a site in NetBox")
        body = {"data": row.data, "sites": [ids[slug] for slug in row.sites]}
        if context is None:
            await nb._request("POST", f"{CONTEXTS}/", json={"name": row.name, "is_active": True,
                                                            "description": PUBLISHED, **body})
            return f"created {row.name} at version {row.version}"
        if row.data != context.get("data") or row.sites != _sites(context):
            await nb._request("PATCH", f"{CONTEXTS}/{context['id']}/", json=body)
            return f"updated {row.name} to version {row.version}"
        return None

    async def _publish(self, nb, name: str) -> None:
        row = (await self.latest()).get(name)
        try:
            found = (await nb._send("GET", f"{CONTEXTS}/", params={"name": name}))["results"]
            await self._put(nb, found[0] if found else None, row)
        except NetBoxError as exc:
            raise NetBoxError(f"{name} is saved in the platform database but was not published to NetBox, so "
                              f"generators still see its old value ({exc}); "
                              "python -m orchestrator.intent_store republish publishes it") from exc

    async def republish(self, nb) -> list[str]:
        """NetBox's config contexts made equal to the newest versions here: what was done."""
        contexts, stored = await asyncio.gather(nb.published_contexts(), self.latest())
        done = [await self._put(nb, context, stored.pop(context["name"], None)) for context in contexts]
        done += [await self._put(nb, None, row) for row in stored.values()]
        return [line for line in done if line]

    async def compare(self, nb) -> list[dict]:
        """Where NetBox's copy and the newest versions here differ."""
        contexts, stored = await asyncio.gather(nb.published_contexts(), self.latest())
        out = []
        for context in contexts:
            row = stored.pop(context["name"], None)
            if row is None or row.data is None:
                out.append({"name": context["name"], "difference": "in NetBox only"})
            elif row.data != context.get("data") or row.sites != _sites(context):
                out.append({"name": context["name"], "difference": "differs", "version": row.version})
        out += [{"name": name, "difference": "not published to NetBox", "version": row.version}
                for name, row in stored.items() if row.data is not None]
        return sorted(out, key=lambda d: d["name"])

    # -- start -------------------------------------------------------------------

    async def start(self, nb) -> list[str]:
        """Attach this store to the NetBox client, after making the two agree: the first time ever
        NetBox's contexts are adopted (they were the source), afterwards NetBox's copy is republished.
        Raises NetBoxError, the store not attached, when the adoption could not be done."""
        async with self.sessions() as session:
            mark = await session.get(Setting, MARK)
            adopted = mark is not None and mark.value == "database"
        if adopted:
            nb.intent = self   # the source whatever NetBox answers now
            return await self.republish(nb)
        contexts, stored = await asyncio.gather(nb.published_contexts(), self.latest())
        done = []
        for context in contexts:
            stored.pop(context["name"], None)
            if await self.record(context["name"], context.get("data"), _sites(context)):
                done.append(f"adopted {context['name']} from NetBox")
        for name in stored:   # recorded once, gone from NetBox since
            if await self.record(name, None, []):
                done.append(f"{name} is no longer in NetBox: recorded as deleted")
        async with self.sessions() as session:
            if (mark := await session.get(Setting, MARK)) is None:
                session.add(Setting(key=MARK, value="database", updated_by="intent store", updated_at=db.now()))
            else:
                mark.value = "database"
            await session.commit()
        nb.intent = self
        return done


def metrics(differences: list[dict] | None) -> bytes:   # /metrics joins bytes (drift.metrics)
    lines = [f"# HELP {METRIC} Intent documents whose published copy in NetBox differs from the platform database.",
             f"# TYPE {METRIC} gauge"]
    if differences is not None:
        lines.append(f"{METRIC} {len(differences)}")
    return ("\n".join(lines) + "\n").encode()


async def _main(command: str) -> int:
    from orchestrator.config import Settings
    from orchestrator.netbox import NetBoxClient

    settings = Settings.from_env()
    engine = db.make_engine(settings.database_url, pooled=False)
    netbox = NetBoxClient(settings.netbox_url, settings.netbox_token, settings.netbox_timeout)
    store = IntentStore(db.make_sessionmaker(engine))
    try:
        if command == "republish":
            for line in await store.start(netbox):   # republishes; adopts instead on an install never started
                print(line)
            return 0
        found = await store.compare(netbox)
        for d in found:
            print(f"{d['name']}: {d['difference']}")
        print(f"{len(found)} differences" if found else "NetBox's copy and the database agree.")
        return 1 if found else 0
    finally:
        await netbox.aclose()
        await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("check", "republish"):
        sys.exit("usage: python -m orchestrator.intent_store check|republish")
    sys.exit(asyncio.run(_main(sys.argv[1])))
