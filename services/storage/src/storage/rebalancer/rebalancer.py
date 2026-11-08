"""Rebalancer: ручной перенос контестов между шардами (docs/tr.md, «Распределение данных по шардам»).

Переносятся только контесты, у которых владелец на новом кольце отличается от шарда, где контест лежит.
Контест переносится целиком:

  1. на старом шарде одной транзакцией ставится migration_state = MIGRATING
     (отправки -> 503, claim/запись результата/Watchdog/пересчёт standings не выполняются, чтение доступно);
  2. все строки контеста (contests, teams, problems, submissions, idempotency_keys, outbox_events, standings)
     копируются на новый шард ОДНОЙ транзакцией; id, статусы, fencing_token и event_id сохраняются;
  3. после COMMIT копия на новом шарде получает migration_state = ACTIVE;
  4. строки контеста на старом шарде удаляются.

Устойчивость: контест, уже скопированный на новый шард, повторно НЕ копируется (чтобы не затереть новые
записи) — выполняется только шаг 4; контест, оставшийся в MIGRATING без копии, переносится заново.
Неопубликованные события outbox переезжают с прежними event_id; возможные дубли допустимы (at-least-once).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from storage.config import StorageSettings
from storage.repositories.errors import StorageError
from storage.shard_router.ring import ShardRing, format_active_shards
from storage.shard_router.router import ShardRouter


class RebalanceError(StorageError):
    """Перенос остановлен; данные источника не тронуты, команду можно запустить повторно."""


@dataclass(frozen=True)
class TableSpec:
    name: str
    where: str  # условие выборки/удаления строк одного контеста (параметр :contest_id)
    json_columns: frozenset[str] = frozenset()


# Порядок — родители раньше детей (для копирования); удаление идёт в обратном порядке.
TABLES: tuple[TableSpec, ...] = (
    TableSpec("contests", "contest_id = :contest_id"),
    TableSpec("teams", "contest_id = :contest_id"),
    TableSpec("problems", "contest_id = :contest_id"),
    TableSpec("submissions", "contest_id = :contest_id"),
    TableSpec(
        "idempotency_keys",
        "team_id IN (SELECT team_id FROM teams WHERE contest_id = :contest_id)",
    ),
    TableSpec(
        "outbox_events",
        "submission_id IN (SELECT submission_id FROM submissions WHERE contest_id = :contest_id)",
        frozenset({"payload"}),
    ),
    TableSpec("standings", "contest_id = :contest_id", frozenset({"rows"})),
)


@dataclass
class MoveResult:
    contest_id: UUID
    source: int
    target: int
    status: str  # planned | copied_and_deleted | already_copied_deleted
    rows: dict[str, int] = field(default_factory=dict)


@dataclass
class RebalanceReport:
    target_shards: tuple[int, ...]
    moves: list[MoveResult]
    unchanged: int

    @property
    def new_active_shards(self) -> str:
        return format_active_shards(self.target_shards)


def _json_value(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value)


class Rebalancer:
    def __init__(self, settings: StorageSettings, router: ShardRouter | None = None) -> None:
        self.settings = settings
        self.router = router or ShardRouter(settings)

    # ------------------------------------------------------------------ команды

    async def add_shard(self, shard_id: int, *, dry_run: bool = False) -> RebalanceReport:
        if shard_id not in self.settings.shards:
            raise RebalanceError(f"Шард {shard_id} не настроен в .env (POSTGRES_SHARD_{shard_id}_*)")
        if shard_id in self.settings.active_shards:
            raise RebalanceError(f"Шард {shard_id} уже в ACTIVE_SHARDS")
        return await self.rebalance(sorted({*self.settings.active_shards, shard_id}), dry_run=dry_run)

    async def remove_shard(self, shard_id: int, *, dry_run: bool = False) -> RebalanceReport:
        if shard_id not in self.settings.active_shards:
            raise RebalanceError(f"Шард {shard_id} не входит в ACTIVE_SHARDS")
        remaining = [shard for shard in self.settings.active_shards if shard != shard_id]
        if not remaining:
            raise RebalanceError("Нельзя удалить последний шард")
        return await self.rebalance(remaining, dry_run=dry_run)

    async def distribution(self) -> dict[int, int]:
        """Сколько контестов лежит на каждом настроенном шарде."""
        result: dict[int, int] = {}
        for shard_id in self.router.configured_shard_ids:
            async with self.router.engine(shard_id).connect() as connection:
                result[shard_id] = (await connection.execute(text("SELECT count(*) FROM contests"))).scalar_one()
        return result

    async def rebalance(self, target_shards: list[int], *, dry_run: bool = False) -> RebalanceReport:
        new_ring = ShardRing(target_shards, self.settings.virtual_nodes)
        moves: list[MoveResult] = []
        unchanged = 0
        for source in self.router.configured_shard_ids:
            async with self.router.engine(source).connect() as connection:
                contest_ids = [
                    UUID(str(value))
                    for value in (
                        await connection.execute(text("SELECT contest_id FROM contests ORDER BY contest_id"))
                    ).scalars()
                ]
            for contest_id in contest_ids:
                target = new_ring.owner(contest_id)
                if target == source:
                    unchanged += 1
                    continue
                if dry_run:
                    moves.append(MoveResult(contest_id, source, target, "planned"))
                else:
                    moves.append(await self._move_contest(contest_id, source, target))
        return RebalanceReport(tuple(sorted(target_shards)), moves, unchanged)

    # ------------------------------------------------------------------ перенос одного контеста

    async def _move_contest(self, contest_id: UUID, source: int, target: int) -> MoveResult:
        source_engine = self.router.engine(source)
        target_engine = self.router.engine(target)
        parameters = {"contest_id": contest_id}

        async with target_engine.connect() as connection:
            already_copied = (
                await connection.execute(
                    text("SELECT 1 FROM contests WHERE contest_id = :contest_id"), parameters
                )
            ).first() is not None

        if already_copied:
            # Копия уже закоммичена целиком (копирование атомарно): только активируем её и чистим источник.
            async with target_engine.begin() as connection:
                await self._set_state(connection, contest_id, "ACTIVE")
            await self._delete_from_source(source, contest_id)
            return MoveResult(contest_id, source, target, "already_copied_deleted")

        # Шаг 1: закрываем запись. UPDATE дожидается транзакций, которые держат контест FOR SHARE.
        async with source_engine.begin() as connection:
            await self._set_state(connection, contest_id, "MIGRATING")

        # Шаг 2: копирование всех строк контеста одной транзакцией на целевом шарде.
        copied: dict[str, int] = {}
        async with source_engine.connect() as source_connection:
            async with target_engine.begin() as target_connection:
                for table in TABLES:
                    copied[table.name] = await self._copy_table(
                        source_connection, target_connection, table, contest_id
                    )

        # Шаг 3: после COMMIT копия становится активной.
        async with target_engine.begin() as connection:
            await self._set_state(connection, contest_id, "ACTIVE")

        # Шаг 4: удаляем строки на старом шарде.
        await self._delete_from_source(source, contest_id)
        return MoveResult(contest_id, source, target, "copied_and_deleted", copied)

    async def _set_state(self, connection: AsyncConnection, contest_id: UUID, state: str) -> None:
        await connection.execute(
            text("UPDATE contests SET migration_state = :state WHERE contest_id = :contest_id"),
            {"state": state, "contest_id": contest_id},
        )

    async def _copy_table(
        self,
        source_connection: AsyncConnection,
        target_connection: AsyncConnection,
        table: TableSpec,
        contest_id: UUID,
    ) -> int:
        parameters = {"contest_id": contest_id}
        rows = (
            await source_connection.execute(
                text(f"SELECT * FROM {table.name} WHERE {table.where}"), parameters
            )
        ).mappings().all()
        if rows:
            columns = list(rows[0].keys())
            placeholders = [
                f"CAST(:{column} AS jsonb)" if column in table.json_columns else f":{column}"
                for column in columns
            ]
            statement = text(
                f"INSERT INTO {table.name} ({', '.join(columns)}) VALUES ({', '.join(placeholders)})"
            )
            await target_connection.execute(
                statement,
                [
                    {
                        column: _json_value(row[column]) if column in table.json_columns else row[column]
                        for column in columns
                    }
                    for row in rows
                ],
            )
        copied = (
            await target_connection.execute(
                text(f"SELECT count(*) FROM {table.name} WHERE {table.where}"), parameters
            )
        ).scalar_one()
        if copied != len(rows):
            raise RebalanceError(
                f"Контест {contest_id}: в {table.name} источник={len(rows)}, приёмник={copied}; "
                "транзакция копирования откатана, данные источника не тронуты"
            )
        return len(rows)

    async def _delete_from_source(self, source: int, contest_id: UUID) -> None:
        async with self.router.engine(source).begin() as connection:
            for table in reversed(TABLES):
                await connection.execute(
                    text(f"DELETE FROM {table.name} WHERE {table.where}"), {"contest_id": contest_id}
                )
