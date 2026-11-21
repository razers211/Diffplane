"""Load a fabric definition into NetBox.

Run inside the orchestrator container (NetBox is not reachable from outside):
    docker compose exec orchestrator python -m seed seed/fabrics/example.yaml
"""

import argparse
import asyncio
import os
import sys

from orchestrator.config import Settings
from orchestrator.netbox import NetBoxClient, NetBoxError
from seed.loader import Seeder, SeedError
from seed.model import FabricError, load


async def rollout_in_progress() -> int | None:
    """The deploy job rolling out, if the platform database is reachable (see deploys.ROLLING_OUT)."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        return None
    from orchestrator import db
    from orchestrator.deploys import rolling_out

    engine = db.make_engine(url, pooled=False)
    try:
        return await rolling_out(db.make_sessionmaker(engine))
    finally:
        await engine.dispose()


async def seed(path: str, force: bool = False) -> int:
    fabric = load(path)
    if not force and (job_id := await rollout_in_progress()) is not None:
        raise SeedError(f"deploy #{job_id} is rolling out; seed again once it finishes (or --force)")
    settings = Settings.from_env()
    netbox = NetBoxClient(settings.netbox_url, settings.netbox_token, settings.netbox_timeout)
    # Intent documents (the fabric's settings) are written to the platform database, which publishes
    # them to NetBox: a seed that wrote them to NetBox alone would be undone at the next start.
    if not settings.database_url:
        raise SeedError("DATABASE_URL is not set: run the seed in the orchestrator container")
    from orchestrator import db
    from orchestrator.intent_store import IntentStore
    engine = db.make_engine(settings.database_url, pooled=False)
    try:
        await IntentStore(db.make_sessionmaker(engine)).start(netbox)
        report = await Seeder(netbox, fabric).run()
    finally:
        await netbox.aclose()
        await engine.dispose()
    for change in report.changes:
        print(change)
    print(f"{len(report.changes)} changes" if report.changes else "No changes.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m seed", description=__doc__.splitlines()[0])
    parser.add_argument("fabric", help="fabric definition YAML")
    parser.add_argument("--force", action="store_true", help="seed even while a deploy is rolling out")
    args = parser.parse_args()
    try:
        return asyncio.run(seed(args.fabric, force=args.force))
    except (FabricError, SeedError, NetBoxError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
