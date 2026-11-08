"""Чтение соревнований, команд и задач на одном шарде."""

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from storage.repositories.models import ContestSchedule
from storage.repositories.submission_repository import _as_uuid, _translated_storage_errors

SELECT_CONTEST_SQL = text(
    """
    SELECT contest_id, name, starts_at, ends_at, is_frozen, migration_state
    FROM contests WHERE contest_id = :contest_id
    """
)
LIST_CONTESTS_SQL = text(
    """
    SELECT contest_id, name, starts_at, ends_at, is_frozen, migration_state
    FROM contests ORDER BY starts_at DESC, contest_id
    """
)
LIST_TEAMS_SQL = text("SELECT team_id, contest_id, name FROM teams WHERE contest_id = :contest_id ORDER BY name")
LIST_PROBLEMS_SQL = text(
    """
    SELECT problem_id, contest_id, code, title, time_limit_ms, memory_limit_kb, test_config_reference
    FROM problems WHERE contest_id = :contest_id ORDER BY code
    """
)


def _to_schedule(row: Any) -> ContestSchedule:
    return ContestSchedule(
        contest_id=_as_uuid(row.contest_id),
        name=row.name,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        is_frozen=row.is_frozen,
        migration_state=row.migration_state,
    )


class ContestRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get_contest_schedule(self, contest_id: UUID) -> ContestSchedule | None:
        """Контест на этом шарде или None. Чтение доступно и при migration_state = MIGRATING."""
        with _translated_storage_errors():
            async with self._engine.connect() as connection:
                row = (await connection.execute(SELECT_CONTEST_SQL, {"contest_id": contest_id})).first()
        return None if row is None else _to_schedule(row)

    async def contest_exists(self, contest_id: UUID) -> bool:
        return await self.get_contest_schedule(contest_id) is not None

    async def list_contests(self) -> list[ContestSchedule]:
        with _translated_storage_errors():
            async with self._engine.connect() as connection:
                rows = (await connection.execute(LIST_CONTESTS_SQL)).all()
        return [_to_schedule(row) for row in rows]

    async def list_teams(self, contest_id: UUID) -> list[dict[str, Any]]:
        with _translated_storage_errors():
            async with self._engine.connect() as connection:
                rows = (await connection.execute(LIST_TEAMS_SQL, {"contest_id": contest_id})).mappings().all()
        return [dict(row) for row in rows]

    async def list_problems(self, contest_id: UUID) -> list[dict[str, Any]]:
        with _translated_storage_errors():
            async with self._engine.connect() as connection:
                rows = (await connection.execute(LIST_PROBLEMS_SQL, {"contest_id": contest_id})).mappings().all()
        return [dict(row) for row in rows]
