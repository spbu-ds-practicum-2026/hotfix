"""Настройки хранилища из переменных окружения (.env.example)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from dotenv import load_dotenv
from sqlalchemy.engine import URL

from storage.shard_router.ring import (
    parse_active_shards,
    virtual_nodes_from_environment,
)


@dataclass(frozen=True)
class ShardSettings:
    index: int
    host: str
    port: int
    database: str
    user: str
    password: str

    @property
    def url(self) -> URL:
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.database,
        )


@dataclass(frozen=True)
class StorageSettings:
    shards: dict[int, ShardSettings]  # все настроенные шарды (в том числе ещё не активные)
    active_shards: tuple[int, ...]  # шарды, на кольце которых сейчас лежат контесты
    virtual_nodes: int
    retry_base_delay_seconds: float
    retry_max_delay_seconds: float


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if not value:
        raise RuntimeError(f"Не задана переменная окружения {name} (см. .env.example)")
    return value


def load_settings(environment: Mapping[str, str] | None = None) -> StorageSettings:
    """Шарды ищутся по POSTGRES_SHARD_<i>_HOST, i = 0, 1, 2, ... подряд."""
    if environment is None:
        load_dotenv()
        environment = os.environ
    shards: dict[int, ShardSettings] = {}
    index = 0
    while f"POSTGRES_SHARD_{index}_HOST" in environment:
        prefix = f"POSTGRES_SHARD_{index}"
        shards[index] = ShardSettings(
            index=index,
            host=_required(environment, f"{prefix}_HOST"),
            port=int(_required(environment, f"{prefix}_PORT")),
            database=_required(environment, f"{prefix}_DB"),
            user=_required(environment, f"{prefix}_USER"),
            password=_required(environment, f"{prefix}_PASSWORD"),
        )
        index += 1
    if not shards:
        raise RuntimeError("Не заданы POSTGRES_SHARD_0_HOST и остальные настройки шардов (см. .env.example)")
    active = parse_active_shards(environment.get("ACTIVE_SHARDS"))
    unknown = [shard_id for shard_id in active if shard_id not in shards]
    if unknown:
        raise RuntimeError(f"ACTIVE_SHARDS ссылается на ненастроенные шарды: {unknown}")
    return StorageSettings(
        shards=shards,
        active_shards=active,
        virtual_nodes=virtual_nodes_from_environment(environment),
        retry_base_delay_seconds=float(environment.get("RETRY_BASE_DELAY_SECONDS") or 2),
        retry_max_delay_seconds=float(environment.get("RETRY_MAX_DELAY_SECONDS") or 30),
    )
