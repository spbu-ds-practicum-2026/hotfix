"""Alembic в async-режиме. Шард выбирается так: alembic -c services/storage/alembic.ini -x shard=1 upgrade head."""

import asyncio

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from storage.database import build_database_url

shard_index = int(context.get_x_argument(as_dictionary=True).get("shard", "0"))


def run_migrations_offline() -> None:
    """Режим --sql: печатает SQL, подключение к БД не нужно."""
    context.configure(
        url="postgresql://", literal_binds=True, dialect_opts={"paramstyle": "named"}
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_with_connection(connection: Connection) -> None:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(build_database_url(shard_index))
    async with engine.connect() as connection:
        await connection.run_sync(run_migrations_with_connection)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
