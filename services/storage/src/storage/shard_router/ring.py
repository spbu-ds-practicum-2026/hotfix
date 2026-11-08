"""Кольцо согласованного хеширования (consistent hashing) с виртуальными узлами.

Ключ маршрутизации — contest_id. Позиция считается по SHA-256, поэтому результат одинаков на любой
машине и в любом процессе (в отличие от встроенного hash()). Физические узлы называются
``postgres_shard_<N>``; по умолчанию активны ``postgres_shard_0`` и ``postgres_shard_1``, состав задаёт
переменная ``ACTIVE_SHARDS`` (одинаковая у всех сервисов), число виртуальных узлов — ``SHARD_VIRTUAL_NODES``.

Модуль не требует паролей и подключения к БД: его безопасно импортировать из domain и API.
"""

from __future__ import annotations

import hashlib
import os
from bisect import bisect_right
from collections.abc import Iterable, Mapping
from functools import lru_cache
from uuid import UUID

SHARD_NAME_PREFIX = "postgres_shard_"
DEFAULT_VIRTUAL_NODES_PER_NODE = 128
DEFAULT_ACTIVE_SHARD_IDS: tuple[int, ...] = (0, 1)
NODE_NAMES = tuple(f"{SHARD_NAME_PREFIX}{index}" for index in DEFAULT_ACTIVE_SHARD_IDS)


def shard_name(shard_id: int) -> str:
    return f"{SHARD_NAME_PREFIX}{shard_id}"


def parse_shard_id(token: str) -> int:
    """'postgres_shard_2' или '2' -> 2."""
    cleaned = token.strip()
    if cleaned.startswith(SHARD_NAME_PREFIX):
        cleaned = cleaned[len(SHARD_NAME_PREFIX):]
    try:
        shard_id = int(cleaned)
    except ValueError:
        raise ValueError(f"Некорректное имя шарда: {token!r}") from None
    if shard_id < 0:
        raise ValueError(f"Номер шарда не может быть отрицательным: {token!r}")
    return shard_id


def parse_active_shards(raw_value: str | None) -> tuple[int, ...]:
    """'postgres_shard_0,postgres_shard_1' -> (0, 1). Пустое значение -> шарды по умолчанию (0, 1)."""
    if raw_value is None or not raw_value.strip():
        return DEFAULT_ACTIVE_SHARD_IDS
    shard_ids = sorted({parse_shard_id(part) for part in raw_value.split(",") if part.strip()})
    return tuple(shard_ids) if shard_ids else DEFAULT_ACTIVE_SHARD_IDS


def format_active_shards(shard_ids: Iterable[int]) -> str:
    """(0, 1, 2) -> 'postgres_shard_0,postgres_shard_1,postgres_shard_2' (формат ACTIVE_SHARDS)."""
    return ",".join(shard_name(shard_id) for shard_id in sorted(set(shard_ids)))


def _stable_position(value: str) -> int:
    return int.from_bytes(hashlib.sha256(value.encode("utf-8")).digest()[:8], "big")


class ConsistentHashRing:
    def __init__(
        self,
        node_names: tuple[str, ...],
        virtual_nodes_per_node: int = DEFAULT_VIRTUAL_NODES_PER_NODE,
    ) -> None:
        if not node_names:
            raise ValueError("Нужен хотя бы один узел")
        if virtual_nodes_per_node < 1:
            raise ValueError("Число виртуальных узлов должно быть положительным")
        points = sorted(
            (_stable_position(f"{node_name}#{index}"), node_name)
            for node_name in node_names
            for index in range(virtual_nodes_per_node)
        )
        self._positions = [position for position, _ in points]
        self._owners = [owner for _, owner in points]

    def get_node(self, key: str) -> str:
        index = bisect_right(self._positions, _stable_position(key)) % len(self._positions)
        return self._owners[index]


class ShardRing:
    """Кольцо над номерами шардов: owner(contest_id) -> номер шарда."""

    def __init__(
        self,
        shard_ids: Iterable[int],
        virtual_nodes_per_node: int = DEFAULT_VIRTUAL_NODES_PER_NODE,
    ) -> None:
        self.shard_ids = tuple(sorted(set(shard_ids)))
        self._ring = ConsistentHashRing(
            tuple(shard_name(shard_id) for shard_id in self.shard_ids), virtual_nodes_per_node
        )

    def owner(self, contest_id: UUID | str) -> int:
        return parse_shard_id(self._ring.get_node(str(UUID(str(contest_id)))))


@lru_cache(maxsize=16)
def _cached_ring(shard_ids: tuple[int, ...], virtual_nodes: int) -> ShardRing:
    return ShardRing(shard_ids, virtual_nodes)


def active_shard_ids_from_environment(environment: Mapping[str, str] | None = None) -> tuple[int, ...]:
    source = os.environ if environment is None else environment
    return parse_active_shards(source.get("ACTIVE_SHARDS"))


def virtual_nodes_from_environment(environment: Mapping[str, str] | None = None) -> int:
    source = os.environ if environment is None else environment
    return int(source.get("SHARD_VIRTUAL_NODES") or DEFAULT_VIRTUAL_NODES_PER_NODE)


def shard_index_for_contest(contest_id: UUID) -> int:
    """Номер шарда, на котором лежат все данные соревнования (по текущему ACTIVE_SHARDS)."""
    ring = _cached_ring(active_shard_ids_from_environment(), virtual_nodes_from_environment())
    return ring.owner(contest_id)
