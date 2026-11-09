"""Маршрутизация по contestId: contestId -> consistent hashing -> шард.

Интерфейс совпадает с тем, что уже используют services/api и services/processing:
``repository_for_contest``, ``contest_repository_for``, ``all_repositories``,
``find_repository_for_submission``, ``from_environment``, ``dispose``.

Правило ТР при переносе контеста: владелец выбирается по текущему кольцу (ACTIVE_SHARDS), а если контеста
на нём ещё нет — ищется на остальных шардах (старый владелец). Если контест лежит на двух шардах
(перенесён, но строки на старом шарде ещё не удалены), используется копия на шарде-владельце кольца,
копия на старом шарде считается устаревшей.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from storage.config import StorageSettings, load_settings
from storage.repositories.contest_repository import ContestRepository
from storage.repositories.models import (
    ContestSchedule,
    CreateSubmissionResult,
    NewOutboxEvent,
    NewSubmission,
    SubmissionRecord,
)
from storage.repositories.submission_repository import SubmissionRepository
from storage.shard_router.ring import ShardRing


class ShardRouter:
    def __init__(self, settings: StorageSettings) -> None:
        self.settings = settings
        self.ring = ShardRing(settings.active_shards, settings.virtual_nodes)
        self._engines: dict[int, AsyncEngine] = {}

    @classmethod
    def from_environment(cls) -> ShardRouter:
        return cls(load_settings())

    # ------------------------------------------------------------------ шарды и движки

    @property
    def configured_shard_ids(self) -> tuple[int, ...]:
        return tuple(sorted(self.settings.shards))

    def shard_for_contest(self, contest_id: UUID) -> int:
        """Владелец контеста по кольцу активных шардов (без обращения к БД)."""
        return self.ring.owner(contest_id)

    def engine(self, shard_id: int) -> AsyncEngine:
        if shard_id not in self.settings.shards:
            raise KeyError(f"Шард {shard_id} не настроен (см. POSTGRES_SHARD_<N>_* в .env)")
        if shard_id not in self._engines:
            self._engines[shard_id] = create_async_engine(self.settings.shards[shard_id].url, pool_pre_ping=True)
        return self._engines[shard_id]

    def submission_repository(self, shard_id: int) -> SubmissionRepository:
        return SubmissionRepository(
            self.engine(shard_id),
            retry_base_delay_seconds=self.settings.retry_base_delay_seconds,
            retry_max_delay_seconds=self.settings.retry_max_delay_seconds,
        )

    def contest_repository(self, shard_id: int) -> ContestRepository:
        return ContestRepository(self.engine(shard_id))

    def all_repositories(self) -> list[SubmissionRepository]:
        """Репозитории всех настроенных шардов: для Outbox publisher и Watchdog."""
        return [self.submission_repository(shard_id) for shard_id in self.configured_shard_ids]

    # ------------------------------------------------------------------ поиск по contestId

    async def find_shard_for_contest(self, contest_id: UUID) -> int:
        """Шард, на котором сейчас лежит контест: владелец по кольцу, иначе любой другой, где контест есть."""
        owner = self.shard_for_contest(contest_id)
        if await self.contest_repository(owner).contest_exists(contest_id):
            return owner
        for shard_id in self.configured_shard_ids:
            if shard_id != owner and await self.contest_repository(shard_id).contest_exists(contest_id):
                return shard_id
        return owner

    def repository_for_contest(self, contest_id: UUID) -> RoutedSubmissionRepository:
        return RoutedSubmissionRepository(self, contest_id)

    def contest_repository_for(self, contest_id: UUID) -> RoutedContestRepository:
        return RoutedContestRepository(self, contest_id)

    # ------------------------------------------------------------------ поиск по submissionId

    async def find_repository_for_submission(self, submission_id: UUID) -> SubmissionRepository | None:
        """Опрашивает шарды по очереди. Если посылка найдена на нескольких, берётся копия на шарде-владельце."""
        found: list[tuple[int, SubmissionRecord]] = []
        for shard_id in self.configured_shard_ids:
            record = await self.submission_repository(shard_id).get_submission_by_id(submission_id)
            if record is not None:
                found.append((shard_id, record))
        if not found:
            return None
        for shard_id, record in found:
            if shard_id == self.shard_for_contest(record.contest_id):
                return self.submission_repository(shard_id)
        return self.submission_repository(found[0][0])

    async def dispose(self) -> None:
        for engine in self._engines.values():
            await engine.dispose()
        self._engines.clear()


class RoutedSubmissionRepository:
    """Репозиторий контеста: шард определяется при каждом вызове (контест мог быть перенесён)."""

    def __init__(self, router: ShardRouter, contest_id: UUID) -> None:
        self._router = router
        self._contest_id = contest_id

    async def create_submission_with_outbox(
        self,
        submission: NewSubmission,
        event: NewOutboxEvent,
        idempotency_key: UUID,
        payload_hash: str,
    ) -> CreateSubmissionResult:
        shard_id = await self._router.find_shard_for_contest(self._contest_id)
        return await self._router.submission_repository(shard_id).create_submission_with_outbox(
            submission, event, idempotency_key, payload_hash
        )

    async def get_submission_by_id(self, submission_id: UUID) -> SubmissionRecord | None:
        repository = await self._router.find_repository_for_submission(submission_id)
        return None if repository is None else await repository.get_submission_by_id(submission_id)


class RoutedContestRepository:
    def __init__(self, router: ShardRouter, contest_id: UUID) -> None:
        self._router = router
        self._contest_id = contest_id

    async def get_contest_schedule(self, contest_id: UUID | None = None) -> ContestSchedule | None:
        target = contest_id or self._contest_id
        shard_id = await self._router.find_shard_for_contest(target)
        return await self._router.contest_repository(shard_id).get_contest_schedule(target)

    async def contest_exists(self, contest_id: UUID | None = None) -> bool:
        return await self.get_contest_schedule(contest_id) is not None


__all__ = ["RoutedContestRepository", "RoutedSubmissionRepository", "ShardRouter"]
