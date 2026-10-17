"""Кольцо согласованного хеширования с виртуальными узлами (предложение для ADR, ещё не согласовано).

Ключ маршрутизации — contest_id. Физические узлы: postgres_shard_0, postgres_shard_1.
Хеш стабилен между процессами и машинами (SHA-256), в отличие от встроенного hash().
"""

import hashlib
from bisect import bisect_right
from uuid import UUID

DEFAULT_VIRTUAL_NODES_PER_NODE = 128
NODE_NAMES = ("postgres_shard_0", "postgres_shard_1")


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
        points = sorted(
            (_stable_position(f"{node_name}#{index}"), node_name)
            for node_name in node_names
            for index in range(virtual_nodes_per_node)
        )
        self._positions = [position for position, _ in points]
        self._owners = [owner for _, owner in points]

    def get_node(self, key: str) -> str:
        index = bisect_right(self._positions, _stable_position(key)) % len(
            self._positions
        )
        return self._owners[index]


_RING = ConsistentHashRing(NODE_NAMES)


def shard_index_for_contest(contest_id: UUID) -> int:
    """0 или 1: номер шарда, на котором лежат все данные соревнования."""
    return NODE_NAMES.index(_RING.get_node(str(contest_id)))
