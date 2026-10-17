"""Согласованное хеширование по contestId (services/storage/src/storage/shard_router/ring.py)."""

from uuid import UUID, uuid4

import pytest
from storage.shard_router.ring import (
    NODE_NAMES,
    ConsistentHashRing,
    shard_index_for_contest,
)


def _keys(count):
    return [str(UUID(int=i * 7919 + 13)) for i in range(count)]


def test_empty_node_list_is_rejected():
    with pytest.raises(ValueError):
        ConsistentHashRing(())


def test_same_key_always_goes_to_same_node():
    ring = ConsistentHashRing(NODE_NAMES)
    key = str(uuid4())
    assert {ring.get_node(key) for _ in range(20)} == {ring.get_node(key)}


def test_two_rings_with_same_nodes_agree():
    first, second = ConsistentHashRing(NODE_NAMES), ConsistentHashRing(NODE_NAMES)
    assert all(first.get_node(k) == second.get_node(k) for k in _keys(500))


def test_single_node_owns_everything():
    ring = ConsistentHashRing(("only",))
    assert {ring.get_node(k) for k in _keys(100)} == {"only"}


def test_keys_are_spread_over_both_shards():
    ring = ConsistentHashRing(NODE_NAMES)
    keys = _keys(2000)
    share = sum(ring.get_node(k) == NODE_NAMES[0] for k in keys) / len(keys)
    assert 0.3 < share < 0.7


def test_adding_a_node_moves_only_part_of_keys_and_only_to_new_node():
    keys = _keys(2000)
    before = ConsistentHashRing(("a", "b"))
    after = ConsistentHashRing(("a", "b", "c"))
    moved = [k for k in keys if before.get_node(k) != after.get_node(k)]
    assert len(moved) / len(keys) < 0.5
    assert all(after.get_node(k) == "c" for k in moved)


def test_shard_index_is_zero_or_one_and_stable():
    indexes = {shard_index_for_contest(UUID(int=i + 1)) for i in range(200)}
    assert indexes == {0, 1}
    contest = UUID("11111111-2222-3333-4444-555555555555")
    assert shard_index_for_contest(contest) == shard_index_for_contest(contest)


def test_shard_index_is_pinned_for_known_contests():
    # Если тест упал — изменилась хеш-функция или параметры кольца: данные в БД окажутся не на своём шарде.
    assert [shard_index_for_contest(UUID(int=i)) for i in range(1, 21)] == [
        0,
        0,
        0,
        0,
        0,
        1,
        1,
        1,
        1,
        1,
        1,
        0,
        1,
        0,
        0,
        0,
        1,
        1,
        1,
        1,
    ]
