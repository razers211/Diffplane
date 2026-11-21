"""Alembic environment: runs migrations on the async engine from DATABASE_URL."""

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from orchestrator.db import Base

config = context.config


def run(connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


async def main() -> None:
    engine = create_async_engine(config.get_main_option("sqlalchemy.url"))
    async with engine.connect() as connection:
        await connection.run_sync(run)
        await connection.commit()
    await engine.dispose()


asyncio.run(main())
