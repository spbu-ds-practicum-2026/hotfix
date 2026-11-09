"""Подключение к PostgreSQL-шардам. Настройки берутся из переменных окружения (.env.example)."""

from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from storage.config import load_settings


def build_database_url(shard_index: int) -> URL:
    settings = load_settings()
    if shard_index not in settings.shards:
        raise ValueError(
            f"Шард {shard_index} не настроен: доступны {sorted(settings.shards)} (см. POSTGRES_SHARD_<N>_*)"
        )
    return settings.shards[shard_index].url


def create_database_engine(shard_index: int) -> AsyncEngine:
    return create_async_engine(build_database_url(shard_index), pool_pre_ping=True)
