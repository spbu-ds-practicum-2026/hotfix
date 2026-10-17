"""Подключение к PostgreSQL-шардам. Настройки берутся из переменных окружения (.env.example)."""

import os

from dotenv import load_dotenv
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

SHARD_COUNT = 2

load_dotenv()


def _read_required_environment_variable(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Не задана переменная окружения {name} (см. .env.example)")
    return value


def build_database_url(shard_index: int) -> URL:
    if shard_index not in range(SHARD_COUNT):
        raise ValueError(
            f"shard_index должен быть от 0 до {SHARD_COUNT - 1}, получено {shard_index}"
        )
    prefix = f"POSTGRES_SHARD_{shard_index}"
    return URL.create(
        drivername="postgresql+asyncpg",
        username=_read_required_environment_variable(f"{prefix}_USER"),
        password=_read_required_environment_variable(f"{prefix}_PASSWORD"),
        host=_read_required_environment_variable(f"{prefix}_HOST"),
        port=int(_read_required_environment_variable(f"{prefix}_PORT")),
        database=_read_required_environment_variable(f"{prefix}_DB"),
    )


def create_database_engine(shard_index: int) -> AsyncEngine:
    return create_async_engine(build_database_url(shard_index), pool_pre_ping=True)
