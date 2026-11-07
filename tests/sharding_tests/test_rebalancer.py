

import random
from uuid import UUID

import pytest

ring_module = pytest.importorskip("storage.shard_router.ring")
errors = pytest.importorskip("contracts.errors")

TWO_NODES = ("postgres_shard_0", "postgres_shard_1")
THREE_NODES = ("postgres_shard_0", "postgres_shard_1", "postgres_shard_2")


def _contest_ids(count: int, seed: int = 2026) -> list[UUID]:
    generator = random.Random(seed)
    return [UUID(int=generator.getrandbits(128), version=4) for _ in range(count)]


class ShardSet:
    

    def __init__(self, node_names: tuple[str, ...], contests: dict[UUID, dict]) -> None:
        self.ring = ring_module.ConsistentHashRing(node_names)
        self.shards: dict[str, dict[UUID, dict]] = {node: {} for node in node_names}
        for contest_id, payload in contests.items():
            self.shards[self.ring.get_node(str(contest_id))][contest_id] = payload

    def owner_of(self, contest_id: UUID) -> str:
        return self.ring.get_node(str(contest_id))

    def contest_count(self) -> int:
        return sum(len(shard) for shard in self.shards.values())

    def checksum(self) -> int:
        return hash(
            tuple(
                sorted(
                    (str(contest_id), repr(sorted(payload.items())))
                    for shard in self.shards.values()
                    for contest_id, payload in shard.items()
                )
            )
        )


class Rebalancer:
    
    def __init__(self, shard_set: ShardSet, new_nodes: tuple[str, ...]) -> None:
        self.shard_set = shard_set
        self.new_nodes = new_nodes
        self.new_ring = ring_module.ConsistentHashRing(new_nodes)
        self.migrating: set[UUID] = set()
        self.copied: set[UUID] = set()

    def _old_owner(self, contest_id: UUID) -> str:
        return self.shard_set.owner_of(contest_id)

    def contests_to_move(self) -> list[UUID]:
        moved = []
        for node, shard in self.shard_set.shards.items():
            if node not in self.new_nodes:
                # узел удаляется: переезжает всё
                moved.extend(shard)
                continue
            for contest_id in shard:
                if self.new_ring.get_node(str(contest_id)) != node:
                    moved.append(contest_id)
        return moved

    def run(self, interrupt_after_copy: bool = False) -> int:
        moved = 0
        for contest_id in self.contests_to_move():
            # 1. на старом шарде: MIGRATING, отправки отклоняются 503
            self.migrating.add(contest_id)
            # 2. копирование на новый шард (идемпотентно: повтор не перезаписывает)
            if contest_id not in self.copied:
                payload = self.shard_set.shards[self._old_owner(contest_id)][contest_id]
                new_owner = self.new_ring.get_node(str(contest_id))
                self.shard_set.shards.setdefault(new_owner, {})[contest_id] = payload
                self.copied.add(contest_id)
                moved += 1
            if interrupt_after_copy:
                # имитация отказа rebalancer после копирования, до удаления
                return moved
            self._finish_contest_migration(contest_id)
        self.shard_set.ring = self.new_ring
        return moved

    def resume(self) -> int:
      
        moved = 0
        for contest_id in list(self.migrating):
            if contest_id not in self.copied:
                payload = self.shard_set.shards[self._old_owner(contest_id)][contest_id]
                new_owner = self.new_ring.get_node(str(contest_id))
                self.shard_set.shards.setdefault(new_owner, {})[contest_id] = payload
                self.copied.add(contest_id)
                moved += 1
            self._finish_contest_migration(contest_id)
        self.shard_set.ring = self.new_ring
        return moved

    def _finish_contest_migration(self, contest_id: UUID) -> None:
        # 3. копия активна, 4. строки на старом шарде удаляются
        old_node = self._old_owner(contest_id)
        new_owner = self.new_ring.get_node(str(contest_id))
        if old_node != new_owner:
            self.shard_set.shards[old_node].pop(contest_id, None)
        self.migrating.discard(contest_id)


def submission_guard(contest_id: UUID, rebalancer: Rebalancer) -> object:
    """Правило из tr.md: отправки в MIGRATING-контест → 503."""
    if contest_id in rebalancer.migrating:
        return errors.temporarily_unavailable()
    return None


def read_guard(shard_set: ShardSet, contest_id: UUID) -> dict | None:
  
    for shard in shard_set.shards.values():
        if contest_id in shard:
            return shard[contest_id]
    return None


def test_contests_are_distributed_across_both_shards():
    contests = {
        cid: {"name": f"contest-{i}"} for i, cid in enumerate(_contest_ids(200))
    }
    shards = ShardSet(TWO_NODES, contests)

    assert shards.contest_count() == 200
   
    assert all(
        sum(1 for shard in shards.shards.values() if cid in shard) == 1
        for cid in contests
    )
    
    assert all(len(shard) > 0 for shard in shards.shards.values())


def test_adding_shard_moves_only_contests_with_changed_owner():
    contest_ids = set(_contest_ids(300))
    contests = {cid: {"name": "c"} for cid in contest_ids}
    shards = ShardSet(TWO_NODES, contests)
    old_ring = ring_module.ConsistentHashRing(TWO_NODES)
    new_ring = ring_module.ConsistentHashRing(THREE_NODES)
    expected_moved = {
        cid
        for cid in contest_ids
        if new_ring.get_node(str(cid)) != old_ring.get_node(str(cid))
    }

    rebalancer = Rebalancer(shards, THREE_NODES)
    moved = rebalancer.run()

    assert moved == len(expected_moved)
    assert 0 < moved < len(contest_ids)  
    assert rebalancer.copied == expected_moved
  
    for cid in contest_ids - expected_moved:
        assert cid in shards.shards[old_ring.get_node(str(cid))]
   
    for cid in contest_ids:
        owners = [node for node, shard in shards.shards.items() if cid in shard]
        assert len(owners) == 1


def test_rerun_moves_nothing_and_loses_nothing():
    contest_ids = _contest_ids(300)
    contests = {
        cid: {"name": f"c-{i}", "rows": [i]} for i, cid in enumerate(contest_ids)
    }
    shards = ShardSet(TWO_NODES, contests)
    checksum_before = shards.checksum()
    total_before = shards.contest_count()

    rebalancer = Rebalancer(shards, THREE_NODES)
    rebalancer.run()

  
    rebalancer_again = Rebalancer(shards, THREE_NODES)
    assert rebalancer_again.contests_to_move() == []
    assert rebalancer_again.run() == 0

    
    assert shards.contest_count() == total_before
    assert shards.checksum() == checksum_before
    for cid in contests:
        owners = [node for node, shard in shards.shards.items() if cid in shard]
        assert len(owners) == 1


def test_interrupted_rebalance_resumes_without_loss_or_duplicates():
    contest_ids = _contest_ids(100)
    contests = {cid: {"name": "c", "submissions": 5} for cid in contest_ids}
    shards = ShardSet(TWO_NODES, contests)

    rebalancer = Rebalancer(shards, THREE_NODES)
    moved_before_failure = rebalancer.run(interrupt_after_copy=True)

    assert moved_before_failure > 0
    assert rebalancer.migrating 
    migrating_contest = next(iter(rebalancer.migrating))
    assert read_guard(shards, migrating_contest) is not None
    guard_error = submission_guard(migrating_contest, rebalancer)
    assert guard_error is not None
    assert guard_error.http_status == 503
    assert guard_error.code.value == "TEMPORARILY_UNAVAILABLE"
  
    non_migrating = next(cid for cid in contest_ids if cid not in rebalancer.migrating)
    assert submission_guard(non_migrating, rebalancer) is None

   
    resumed = rebalancer.resume()

    assert resumed == 0  
    assert not rebalancer.migrating
   
    assert shards.contest_count() == 100
    for cid in contest_ids:
        owners = [node for node, shard in shards.shards.items() if cid in shard]
        assert len(owners) == 1


def test_distribution_is_deterministic_between_runs():
    contest_ids = _contest_ids(50)
    first = ShardSet(TWO_NODES, {cid: {} for cid in contest_ids})
    second = ShardSet(TWO_NODES, {cid: {} for cid in contest_ids})

    for cid in contest_ids:
        assert first.owner_of(cid) == second.owner_of(cid)
