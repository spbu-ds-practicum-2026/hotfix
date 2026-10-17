"""Репозиторий отправок и Outbox.

Вызывающему коду не нужно знать про таблицы и SQL. Все методы асинхронные, потому что стек: SQLAlchemy 2 + asyncpg.
Один экземпляр репозитория работает с одним шардом (одним AsyncEngine). Выбор шарда по contest_id — задача shard_router.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from storage.enums import Language, SubmissionStatus, Verdict
from storage.repositories.errors import (
    DuplicateRecordError,
    IdempotencyKeyReusedError,
    ReferenceNotFoundError,
    StorageUnavailableError,
)
from storage.repositories.models import (
    CreateSubmissionResult,
    NewOutboxEvent,
    NewSubmission,
    OutboxEventRecord,
    SubmissionRecord,
)

SUBMISSION_COLUMNS = """
    submission_id, contest_id, team_id, problem_id, trace_id, source_code, language, status, verdict,
    sequence, fencing_token, attempt_count, lease_until, worker_id, next_attempt_at, last_error,
    created_at, updated_at
"""

OUTBOX_COLUMNS = """
    event_id, submission_id, trace_id, event_type, routing_key, schema_version, payload,
    created_at, published_at, attempt_count, last_error
"""

CLAIM_IDEMPOTENCY_KEY_SQL = text(
    """
    INSERT INTO idempotency_keys (team_id, idempotency_key, payload_hash, submission_id)
    VALUES (:team_id, :idempotency_key, :payload_hash, :submission_id)
    ON CONFLICT (team_id, idempotency_key) DO NOTHING
    RETURNING submission_id
    """
)

SELECT_IDEMPOTENCY_KEY_SQL = text(
    """
    SELECT payload_hash, submission_id
    FROM idempotency_keys
    WHERE team_id = :team_id AND idempotency_key = :idempotency_key
    """
)

INSERT_SUBMISSION_SQL = text(
    """
    INSERT INTO submissions (
        submission_id, contest_id, team_id, problem_id, trace_id, source_code, language,
        status, sequence, fencing_token, attempt_count, created_at, updated_at
    )
    VALUES (
        :submission_id, :contest_id, :team_id, :problem_id, :trace_id, :source_code, :language,
        'QUEUED', 1, 0, 0, :created_at, :created_at
    )
    """
)

INSERT_OUTBOX_EVENT_SQL = text(
    """
    INSERT INTO outbox_events (
        event_id, submission_id, trace_id, event_type, routing_key, schema_version, payload, created_at
    )
    VALUES (
        :event_id, :submission_id, :trace_id, :event_type, :routing_key, :schema_version, :payload, :created_at
    )
    """
).bindparams(bindparam("payload", type_=JSONB))

SELECT_SUBMISSION_SQL = text(f"SELECT {SUBMISSION_COLUMNS} FROM submissions WHERE submission_id = :submission_id")

SELECT_PENDING_OUTBOX_EVENTS_SQL = text(
    f"""
    SELECT {OUTBOX_COLUMNS}
    FROM outbox_events
    WHERE published_at IS NULL
    ORDER BY created_at, event_id
    LIMIT :limit
    """
)

MARK_OUTBOX_EVENT_PUBLISHED_SQL = text(
    """
    UPDATE outbox_events
    SET published_at = now()
    WHERE event_id = :event_id AND published_at IS NULL
    """
)

RECORD_OUTBOX_PUBLISH_FAILURE_SQL = text(
    """
    UPDATE outbox_events
    SET attempt_count = attempt_count + 1, last_error = :error_message
    WHERE event_id = :event_id AND published_at IS NULL
    """
)


@contextmanager
def _translated_storage_errors() -> Iterator[None]:
    """Превращает ошибки SQLAlchemy/сети в исключения из storage.repositories.errors."""
    try:
        yield
    except IntegrityError as error:
        raise _translate_integrity_error(error) from error
    except (OperationalError, InterfaceError, OSError) as error:
        raise StorageUnavailableError(f"База данных недоступна: {error}") from error


def _translate_integrity_error(error: IntegrityError) -> Exception:
    message = str(error.orig)
    if "violates foreign key constraint" in message:
        if "fk_submissions_team" in message or "fk_idempotency_keys_team" in message:
            return ReferenceNotFoundError("team", "Команда не найдена или не принадлежит соревнованию")
        if "fk_submissions_problem" in message:
            return ReferenceNotFoundError("problem", "Задача не найдена или не принадлежит соревнованию")
    if "duplicate key value" in message:
        return DuplicateRecordError(f"Запись уже существует: {message}")
    return error


def _as_uuid(value: Any) -> UUID:
    return value if type(value) is UUID else UUID(str(value))


def _as_optional_uuid(value: Any) -> UUID | None:
    return None if value is None else _as_uuid(value)


def _as_payload(value: Any) -> dict[str, Any]:
    return json.loads(value) if isinstance(value, str) else value


def _to_submission_record(row: Any) -> SubmissionRecord:
    values = row._mapping
    return SubmissionRecord(
        submission_id=_as_uuid(values["submission_id"]),
        contest_id=_as_uuid(values["contest_id"]),
        team_id=_as_uuid(values["team_id"]),
        problem_id=_as_uuid(values["problem_id"]),
        trace_id=_as_uuid(values["trace_id"]),
        source_code=values["source_code"],
        language=Language(values["language"]),
        status=SubmissionStatus(values["status"]),
        verdict=Verdict(values["verdict"]) if values["verdict"] is not None else None,
        sequence=values["sequence"],
        fencing_token=values["fencing_token"],
        attempt_count=values["attempt_count"],
        lease_until=values["lease_until"],
        worker_id=_as_optional_uuid(values["worker_id"]),
        next_attempt_at=values["next_attempt_at"],
        last_error=values["last_error"],
        created_at=values["created_at"],
        updated_at=values["updated_at"],
    )


def _to_outbox_event_record(row: Any) -> OutboxEventRecord:
    values = row._mapping
    return OutboxEventRecord(
        event_id=_as_uuid(values["event_id"]),
        submission_id=_as_uuid(values["submission_id"]),
        trace_id=_as_uuid(values["trace_id"]),
        event_type=values["event_type"],
        routing_key=values["routing_key"],
        schema_version=values["schema_version"],
        payload=_as_payload(values["payload"]),
        created_at=values["created_at"],
        published_at=values["published_at"],
        attempt_count=values["attempt_count"],
        last_error=values["last_error"],
    )


class SubmissionRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create_submission_with_outbox(
        self,
        submission: NewSubmission,
        event: NewOutboxEvent,
        idempotency_key: UUID,
        payload_hash: str,
    ) -> CreateSubmissionResult:
        """Атомарно сохраняет отправку, ключ идемпотентности и событие submission.queued.v1.

        Всё выполняется в одной транзакции: либо записаны все три строки, либо ни одной.

        Возвращает CreateSubmissionResult(is_new=True) при успехе. Если пара (team_id, idempotency_key)
        уже занята и payload_hash совпадает, возвращает существующую отправку с is_new=False и ничего не пишет.

        Исключения:
            IdempotencyKeyReusedError  — ключ занят, но хеш тела другой (409).
            ReferenceNotFoundError     — команда/задача не найдена или из другого соревнования (404).
            DuplicateRecordError       — повторный submission_id или event_id; транзакция откачена.
            StorageUnavailableError    — база недоступна (503).
        """
        with _translated_storage_errors():
            async with self._engine.begin() as connection:
                claimed = await connection.execute(
                    CLAIM_IDEMPOTENCY_KEY_SQL,
                    {
                        "team_id": submission.team_id,
                        "idempotency_key": idempotency_key,
                        "payload_hash": payload_hash,
                        "submission_id": submission.submission_id,
                    },
                )
                if claimed.first() is None:
                    return await self._return_existing_submission(
                        connection, submission.team_id, idempotency_key, payload_hash
                    )

                await connection.execute(
                    INSERT_SUBMISSION_SQL,
                    {
                        "submission_id": submission.submission_id,
                        "contest_id": submission.contest_id,
                        "team_id": submission.team_id,
                        "problem_id": submission.problem_id,
                        "trace_id": submission.trace_id,
                        "source_code": submission.source_code,
                        "language": submission.language.value,
                        "created_at": submission.created_at,
                    },
                )
                await connection.execute(
                    INSERT_OUTBOX_EVENT_SQL,
                    {
                        "event_id": event.event_id,
                        "submission_id": event.submission_id,
                        "trace_id": event.trace_id,
                        "event_type": event.event_type,
                        "routing_key": event.routing_key,
                        "schema_version": event.schema_version,
                        "payload": event.payload,
                        "created_at": event.created_at,
                    },
                )
                created = await connection.execute(SELECT_SUBMISSION_SQL, {"submission_id": submission.submission_id})
                return CreateSubmissionResult(submission=_to_submission_record(created.one()), is_new=True)

    async def get_submission_by_id(self, submission_id: UUID) -> SubmissionRecord | None:
        """Возвращает отправку или None, если такой нет на этом шарде."""
        with _translated_storage_errors():
            async with self._engine.connect() as connection:
                result = await connection.execute(SELECT_SUBMISSION_SQL, {"submission_id": submission_id})
                row = result.first()
                return None if row is None else _to_submission_record(row)

    async def get_pending_outbox_events(self, limit: int) -> list[OutboxEventRecord]:
        """Неопубликованные события, старые первыми.

        Доставка at-least-once: между чтением и mark_outbox_event_published процесс может упасть,
        и событие уйдёт повторно. Потребители дедуплицируют по eventId и sequence.
        """
        if limit < 1:
            raise ValueError("limit должен быть положительным")
        with _translated_storage_errors():
            async with self._engine.connect() as connection:
                result = await connection.execute(SELECT_PENDING_OUTBOX_EVENTS_SQL, {"limit": limit})
                return [_to_outbox_event_record(row) for row in result]

    async def mark_outbox_event_published(self, event_id: UUID) -> bool:
        """Помечает событие опубликованным после publisher confirm.

        True — событие помечено сейчас; False — оно уже было помечено (или не существует). Повторный вызов безопасен.
        """
        with _translated_storage_errors():
            async with self._engine.begin() as connection:
                result = await connection.execute(MARK_OUTBOX_EVENT_PUBLISHED_SQL, {"event_id": event_id})
                return result.rowcount == 1

    async def record_outbox_publish_failure(self, event_id: UUID, error_message: str) -> None:
        """Увеличивает attempt_count и сохраняет текст ошибки публикации. Событие остаётся в очереди на отправку."""
        with _translated_storage_errors():
            async with self._engine.begin() as connection:
                await connection.execute(
                    RECORD_OUTBOX_PUBLISH_FAILURE_SQL, {"event_id": event_id, "error_message": error_message}
                )

    async def _return_existing_submission(
        self,
        connection: AsyncConnection,
        team_id: UUID,
        idempotency_key: UUID,
        payload_hash: str,
    ) -> CreateSubmissionResult:
        key_row = (
            await connection.execute(
                SELECT_IDEMPOTENCY_KEY_SQL, {"team_id": team_id, "idempotency_key": idempotency_key}
            )
        ).one()
        if key_row.payload_hash != payload_hash:
            raise IdempotencyKeyReusedError("Idempotency-Key уже использован с другим телом запроса")
        existing = await connection.execute(SELECT_SUBMISSION_SQL, {"submission_id": key_row.submission_id})
        return CreateSubmissionResult(submission=_to_submission_record(existing.one()), is_new=False)
