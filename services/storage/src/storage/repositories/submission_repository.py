"""Репозиторий отправок и Outbox одного шарда (один экземпляр = один AsyncEngine).

Правила из docs/tr.md, которые здесь реализованы:
* создание: ключ идемпотентности + посылка QUEUED + событие submission.queued.v1 — одна транзакция;
* захват (claim) возможен только из QUEUED и только если контест ACTIVE; fencing_token растёт;
* ЛЮБОЙ переход worker'а (COMPLETED, RETRYING, SYSTEM_ERROR) — один условный UPDATE:
  совпадение fencing_token + статус PROCESSING + lease_until > now(); иначе stale_token, ничего не меняется;
* submission.verdict.v1 пишется в Outbox в той же транзакции, что и результат; при SYSTEM_ERROR события нет;
* Watchdog: PROCESSING с истёкшей арендой -> RETRYING (или SYSTEM_ERROR по лимиту попыток),
  RETRYING с наступившим next_attempt_at -> QUEUED + queued-событие в той же транзакции;
* контест в MIGRATING: создание/захват отклоняются (503), записи старого worker'а отбрасываются,
  Watchdog такой контест не трогает. Контест блокируется FOR SHARE, поэтому Rebalancer, переключая
  MIGRATING, дожидается уже начатых записей, а новые видят MIGRATING.
"""

import json
import random
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from storage.enums import Language, SubmissionStatus, Verdict
from storage.repositories.errors import (
    ContestMigratingError,
    ContestUnavailableError,
    DuplicateRecordError,
    IdempotencyKeyReusedError,
    ReferenceNotFoundError,
    StorageUnavailableError,
)
from storage.repositories.models import (
    ClaimedSubmission,
    CreateSubmissionResult,
    JudgingData,
    NewOutboxEvent,
    NewSubmission,
    OutboxEventRecord,
    RecoveredSubmission,
    SubmissionRecord,
    WriteResultOutcome,
    attempt_id_for,
)

ACTIVE = "ACTIVE"

SUBMISSION_COLUMNS = """
    submission_id, contest_id, team_id, problem_id, trace_id, source_code, language, status, verdict,
    sequence, fencing_token, attempt_count, lease_until, worker_id, next_attempt_at, last_error,
    submitted_at, created_at, updated_at
"""

OUTBOX_COLUMNS = """
    event_id, submission_id, trace_id, event_type, routing_key, schema_version, payload,
    created_at, published_at, attempt_count, last_error
"""

LOCK_CONTEST_SQL = text("SELECT migration_state FROM contests WHERE contest_id = :contest_id FOR SHARE")

SELECT_CONTEST_OF_SUBMISSION_SQL = text(
    "SELECT contest_id FROM submissions WHERE submission_id = :submission_id"
)

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
        status, sequence, fencing_token, attempt_count, submitted_at, created_at, updated_at
    )
    VALUES (
        :submission_id, :contest_id, :team_id, :problem_id, :trace_id, :source_code, :language,
        'QUEUED', 1, 0, 0, :created_at, :created_at, :created_at
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

SELECT_SUBMISSION_SQL = text(
    f"SELECT {SUBMISSION_COLUMNS} FROM submissions WHERE submission_id = :submission_id"
)

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

# Захват: только из QUEUED. attempt_count считает попытки обработки (1 для первой).
CLAIM_SQL = text(
    """
    UPDATE submissions
    SET status = 'PROCESSING',
        fencing_token = fencing_token + 1,
        attempt_count = attempt_count + 1,
        sequence = sequence + 1,
        worker_id = :worker_id,
        lease_until = now() + CAST(:lease_seconds AS double precision) * interval '1 second',
        next_attempt_at = NULL
    WHERE submission_id = :submission_id AND status = 'QUEUED'
    RETURNING fencing_token, attempt_count, lease_until
    """
)

# Условие владения посылкой для ЛЮБОГО перехода worker'а (docs/tr.md: «Обработка отказов worker'а и Judge0»).
OWNERSHIP_CONDITION = """
    submission_id = :submission_id
    AND fencing_token = :fencing_token
    AND status = 'PROCESSING'
    AND lease_until > now()
"""

EXTEND_LEASE_SQL = text(
    f"""
    UPDATE submissions
    SET lease_until = now() + CAST(:lease_seconds AS double precision) * interval '1 second'
    WHERE {OWNERSHIP_CONDITION}
    """
)

WRITE_RESULT_SQL = text(
    f"""
    UPDATE submissions
    SET status = 'COMPLETED', verdict = :verdict, sequence = sequence + 1,
        lease_until = NULL, next_attempt_at = NULL
    WHERE {OWNERSHIP_CONDITION}
    RETURNING contest_id, team_id, problem_id, trace_id, sequence, fencing_token, submitted_at
    """
)

MARK_RETRYING_SQL = text(
    f"""
    UPDATE submissions
    SET status = 'RETRYING', sequence = sequence + 1, next_attempt_at = :next_attempt_at,
        last_error = :reason, lease_until = NULL, worker_id = NULL
    WHERE {OWNERSHIP_CONDITION}
    """
)

MARK_SYSTEM_ERROR_SQL = text(
    f"""
    UPDATE submissions
    SET status = 'SYSTEM_ERROR', verdict = NULL, sequence = sequence + 1, last_error = :reason,
        lease_until = NULL, next_attempt_at = NULL, worker_id = NULL
    WHERE {OWNERSHIP_CONDITION}
    """
)

SELECT_EXPIRED_SQL = text(
    """
    SELECT submission_id, contest_id, attempt_count
    FROM submissions
    WHERE status = 'PROCESSING' AND lease_until < now()
    ORDER BY lease_until
    LIMIT :limit
    FOR UPDATE SKIP LOCKED
    """
)

RECOVER_TO_RETRYING_SQL = text(
    """
    UPDATE submissions
    SET status = 'RETRYING', sequence = sequence + 1, next_attempt_at = :next_attempt_at,
        last_error = 'lease expired', lease_until = NULL, worker_id = NULL
    WHERE submission_id = :submission_id AND status = 'PROCESSING' AND lease_until < now()
    """
)

RECOVER_TO_SYSTEM_ERROR_SQL = text(
    """
    UPDATE submissions
    SET status = 'SYSTEM_ERROR', verdict = NULL, sequence = sequence + 1,
        last_error = 'lease expired, attempts exhausted', lease_until = NULL, worker_id = NULL
    WHERE submission_id = :submission_id AND status = 'PROCESSING' AND lease_until < now()
    """
)

SELECT_DUE_RETRIES_SQL = text(
    """
    SELECT submission_id, contest_id, trace_id
    FROM submissions
    WHERE status = 'RETRYING' AND next_attempt_at <= now()
    ORDER BY next_attempt_at
    LIMIT :limit
    FOR UPDATE SKIP LOCKED
    """
)

REQUEUE_SQL = text(
    """
    UPDATE submissions
    SET status = 'QUEUED', sequence = sequence + 1, next_attempt_at = NULL
    WHERE submission_id = :submission_id AND status = 'RETRYING' AND next_attempt_at <= now()
    RETURNING sequence
    """
)

LOAD_FOR_JUDGING_SQL = text(
    """
    SELECT s.source_code, s.language, p.time_limit_ms, p.memory_limit_kb, p.test_config_reference
    FROM submissions AS s
    JOIN problems AS p ON p.problem_id = s.problem_id AND p.contest_id = s.contest_id
    WHERE s.submission_id = :submission_id
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
        submitted_at=values["submitted_at"],
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


async def _contest_state(connection: AsyncConnection, contest_id: UUID) -> str | None:
    """Блокирует строку контеста FOR SHARE и возвращает migration_state (None — контеста на шарде нет)."""
    return (await connection.execute(LOCK_CONTEST_SQL, {"contest_id": contest_id})).scalar_one_or_none()


async def _contest_id_of(connection: AsyncConnection, submission_id: UUID) -> UUID | None:
    value = (
        await connection.execute(SELECT_CONTEST_OF_SUBMISSION_SQL, {"submission_id": submission_id})
    ).scalar_one_or_none()
    return None if value is None else _as_uuid(value)


async def _insert_outbox_event(connection: AsyncConnection, event: NewOutboxEvent) -> None:
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


class SubmissionRepository:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        retry_base_delay_seconds: float = 2.0,
        retry_max_delay_seconds: float = 30.0,
        random_generator: random.Random | None = None,
    ) -> None:
        self._engine = engine
        self._retry_base_delay_seconds = retry_base_delay_seconds
        self._retry_max_delay_seconds = retry_max_delay_seconds
        self._random = random_generator or random.Random()

    # ------------------------------------------------------------------ API (№2)

    async def create_submission_with_outbox(
        self,
        submission: NewSubmission,
        event: NewOutboxEvent,
        idempotency_key: UUID,
        payload_hash: str,
    ) -> CreateSubmissionResult:
        """Атомарно сохраняет отправку, ключ идемпотентности и событие submission.queued.v1.

        Всё выполняется в одной транзакции: либо записаны все три строки, либо ни одной.
        Порядок по ТР: сначала ищется ключ идемпотентности (повтор допустим и после endsAt), и только
        потом ключ занимается (INSERT ... ON CONFLICT DO NOTHING) вместе с созданием посылки.

        Возвращает CreateSubmissionResult(is_new=True) при успехе. Если пара (team_id, idempotency_key)
        уже занята и payload_hash совпадает, возвращает существующую отправку с is_new=False и ничего не пишет.

        Исключения:
            IdempotencyKeyReusedError  — ключ занят, но хеш тела другой (409).
            ReferenceNotFoundError     — команда/задача не найдена или из другого соревнования (404).
            ContestMigratingError      — контест переносится между шардами (503).
            ContestUnavailableError    — контеста нет на этом шарде (503, повторить позже).
            DuplicateRecordError       — повторный submission_id или event_id; транзакция откачена.
            StorageUnavailableError    — база недоступна (503).
        """
        with _translated_storage_errors():
            async with self._engine.begin() as connection:
                state = await _contest_state(connection, submission.contest_id)
                if state is None:
                    raise ContestUnavailableError("Контест не найден на этом шарде")
                if state != ACTIVE:
                    raise ContestMigratingError("Контест переносится на другой шард")

                existing = await self._find_existing_by_key(
                    connection, submission.team_id, idempotency_key, payload_hash
                )
                if existing is not None:
                    return existing

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
                    # Параллельный запрос с тем же ключом успел раньше и уже закоммитился.
                    existing = await self._find_existing_by_key(
                        connection, submission.team_id, idempotency_key, payload_hash
                    )
                    if existing is None:
                        raise DuplicateRecordError("Не удалось занять ключ идемпотентности")
                    return existing

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
                await _insert_outbox_event(connection, event)
                created = await connection.execute(
                    SELECT_SUBMISSION_SQL, {"submission_id": submission.submission_id}
                )
                return CreateSubmissionResult(submission=_to_submission_record(created.one()), is_new=True)

    async def get_submission_by_id(self, submission_id: UUID) -> SubmissionRecord | None:
        """Возвращает отправку или None, если такой нет на этом шарде."""
        with _translated_storage_errors():
            async with self._engine.connect() as connection:
                result = await connection.execute(SELECT_SUBMISSION_SQL, {"submission_id": submission_id})
                row = result.first()
                return None if row is None else _to_submission_record(row)

    # ------------------------------------------------------------------ Outbox publisher (№2)

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
        """Увеличивает attempt_count и сохраняет текст ошибки. Событие остаётся неопубликованным."""
        with _translated_storage_errors():
            async with self._engine.begin() as connection:
                await connection.execute(
                    RECORD_OUTBOX_PUBLISH_FAILURE_SQL,
                    {"event_id": event_id, "error_message": error_message},
                )

    # ------------------------------------------------------------------ Worker (№2)

    async def claim_submission(
        self, submission_id: UUID, worker_id: UUID, lease_seconds: int
    ) -> ClaimedSubmission | None:
        """Атомарный захват: только из QUEUED, только при ACTIVE-контесте.

        None — захват невозможен (финальный статус, уже захвачена, RETRYING до next_attempt_at, нет посылки);
        сообщение подтверждается без обработки. ContestMigratingError — контест переносится: сообщение
        нужно вернуть в очередь с задержкой (NACK).
        """
        with _translated_storage_errors():
            async with self._engine.begin() as connection:
                contest_id = await _contest_id_of(connection, submission_id)
                if contest_id is None:
                    return None
                state = await _contest_state(connection, contest_id)
                if state is None:
                    return None
                if state != ACTIVE:
                    raise ContestMigratingError("Контест переносится на другой шард")
                row = (
                    await connection.execute(
                        CLAIM_SQL,
                        {
                            "submission_id": submission_id,
                            "worker_id": worker_id,
                            "lease_seconds": float(lease_seconds),
                        },
                    )
                ).first()
                if row is None:
                    return None
                return ClaimedSubmission(
                    submission_id=submission_id,
                    attempt_id=attempt_id_for(submission_id, row.fencing_token),
                    attempt_count=row.attempt_count,
                    fencing_token=row.fencing_token,
                    lease_until=row.lease_until,
                )

    async def extend_lease(self, submission_id: UUID, fencing_token: int, lease_seconds: int) -> bool:
        """Heartbeat. False — аренда потеряна (токен устарел, аренда истекла или контест переносится)."""
        with _translated_storage_errors():
            async with self._engine.begin() as connection:
                if not await self._contest_is_active(connection, submission_id):
                    return False
                result = await connection.execute(
                    EXTEND_LEASE_SQL,
                    {
                        "submission_id": submission_id,
                        "fencing_token": fencing_token,
                        "lease_seconds": float(lease_seconds),
                    },
                )
                return result.rowcount == 1

    async def write_result(
        self, submission_id: UUID, fencing_token: int, verdict: Verdict
    ) -> WriteResultOutcome:
        """writeResult(...) -> success | stale_token.

        Один условный UPDATE (fencing_token + PROCESSING + lease_until > now()) и событие
        submission.verdict.v1 в Outbox в той же транзакции. Если строка не обновлена — ничего не пишется.
        """
        from storage.outbox.events import build_submission_verdict_event  # noqa: PLC0415 - избегаем цикла импортов

        with _translated_storage_errors():
            async with self._engine.begin() as connection:
                if not await self._contest_is_active(connection, submission_id):
                    return WriteResultOutcome.STALE_TOKEN
                row = (
                    await connection.execute(
                        WRITE_RESULT_SQL,
                        {
                            "submission_id": submission_id,
                            "fencing_token": fencing_token,
                            "verdict": verdict.value,
                        },
                    )
                ).first()
                if row is None:
                    return WriteResultOutcome.STALE_TOKEN
                await _insert_outbox_event(
                    connection,
                    build_submission_verdict_event(
                        submission_id=submission_id,
                        contest_id=_as_uuid(row.contest_id),
                        team_id=_as_uuid(row.team_id),
                        problem_id=_as_uuid(row.problem_id),
                        trace_id=_as_uuid(row.trace_id),
                        occurred_at=datetime.now(UTC),
                        submitted_at=row.submitted_at,
                        fencing_token=row.fencing_token,
                        status=SubmissionStatus.COMPLETED,
                        verdict=verdict,
                        sequence=row.sequence,
                    ),
                )
                return WriteResultOutcome.SUCCESS

    async def mark_retrying(
        self, submission_id: UUID, fencing_token: int, reason: str, next_attempt_at: datetime
    ) -> bool:
        """PROCESSING -> RETRYING с задержкой до next_attempt_at. False — worker потерял владение (stale_token)."""
        return await self._conditional_transition(
            MARK_RETRYING_SQL,
            submission_id,
            {"fencing_token": fencing_token, "reason": reason, "next_attempt_at": next_attempt_at},
        )

    async def mark_system_error(self, submission_id: UUID, fencing_token: int, reason: str) -> bool:
        """PROCESSING -> SYSTEM_ERROR без вердикта и без события вердикта. False — stale_token."""
        return await self._conditional_transition(
            MARK_SYSTEM_ERROR_SQL, submission_id, {"fencing_token": fencing_token, "reason": reason}
        )

    # ------------------------------------------------------------------ Watchdog (№2)

    async def recover_expired(self, limit: int, max_attempts: int) -> list[RecoveredSubmission]:
        """PROCESSING с истёкшей арендой -> RETRYING (с задержкой) или SYSTEM_ERROR, если попытки исчерпаны.

        Контесты в MIGRATING пропускаются. fencing_token не меняется: старый worker получит stale_token,
        так как статус больше не PROCESSING, а следующий claim выдаст токен + 1.
        """
        recovered: list[RecoveredSubmission] = []
        with _translated_storage_errors():
            async with self._engine.begin() as connection:
                rows = (await connection.execute(SELECT_EXPIRED_SQL, {"limit": limit})).all()
                for row in rows:
                    submission_id = _as_uuid(row.submission_id)
                    if await _contest_state(connection, _as_uuid(row.contest_id)) != ACTIVE:
                        continue
                    if row.attempt_count >= max_attempts:
                        updated = await connection.execute(
                            RECOVER_TO_SYSTEM_ERROR_SQL, {"submission_id": submission_id}
                        )
                        status = SubmissionStatus.SYSTEM_ERROR
                    else:
                        updated = await connection.execute(
                            RECOVER_TO_RETRYING_SQL,
                            {
                                "submission_id": submission_id,
                                "next_attempt_at": datetime.now(UTC)
                                + timedelta(seconds=self._backoff_delay_seconds(row.attempt_count)),
                            },
                        )
                        status = SubmissionStatus.RETRYING
                    if updated.rowcount == 1:
                        recovered.append(RecoveredSubmission(submission_id=submission_id, status=status))
        return recovered

    async def enqueue_due_retries(self, limit: int) -> int:
        """RETRYING с наступившим next_attempt_at -> QUEUED + событие submission.queued.v1 (одна транзакция)."""
        from storage.outbox.events import build_submission_queued_event  # noqa: PLC0415 - избегаем цикла импортов

        requeued = 0
        with _translated_storage_errors():
            async with self._engine.begin() as connection:
                rows = (await connection.execute(SELECT_DUE_RETRIES_SQL, {"limit": limit})).all()
                for row in rows:
                    submission_id = _as_uuid(row.submission_id)
                    if await _contest_state(connection, _as_uuid(row.contest_id)) != ACTIVE:
                        continue
                    sequence = (
                        await connection.execute(REQUEUE_SQL, {"submission_id": submission_id})
                    ).scalar_one_or_none()
                    if sequence is None:
                        continue
                    await _insert_outbox_event(
                        connection,
                        build_submission_queued_event(
                            submission_id=submission_id,
                            trace_id=_as_uuid(row.trace_id),
                            occurred_at=datetime.now(UTC),
                            sequence=sequence,
                        ),
                    )
                    requeued += 1
        return requeued

    async def load_for_judging(self, submission_id: UUID) -> JudgingData | None:
        """Код, язык, лимиты и ссылка на тесты задачи; None, если посылки нет."""
        with _translated_storage_errors():
            async with self._engine.connect() as connection:
                row = (await connection.execute(LOAD_FOR_JUDGING_SQL, {"submission_id": submission_id})).first()
        if row is None:
            return None
        return JudgingData(
            source_code=row.source_code,
            language=Language(row.language),
            time_limit_ms=row.time_limit_ms,
            memory_limit_kb=row.memory_limit_kb,
            test_config_reference=row.test_config_reference,
        )

    # ------------------------------------------------------------------ внутреннее

    async def _find_existing_by_key(
        self, connection: AsyncConnection, team_id: UUID, idempotency_key: UUID, payload_hash: str
    ) -> CreateSubmissionResult | None:
        key_row = (
            await connection.execute(
                SELECT_IDEMPOTENCY_KEY_SQL, {"team_id": team_id, "idempotency_key": idempotency_key}
            )
        ).first()
        if key_row is None:
            return None
        if key_row.payload_hash != payload_hash:
            raise IdempotencyKeyReusedError("Idempotency-Key уже использован с другим телом запроса")
        existing = await connection.execute(SELECT_SUBMISSION_SQL, {"submission_id": key_row.submission_id})
        return CreateSubmissionResult(submission=_to_submission_record(existing.one()), is_new=False)

    async def _contest_is_active(self, connection: AsyncConnection, submission_id: UUID) -> bool:
        contest_id = await _contest_id_of(connection, submission_id)
        if contest_id is None:
            return False
        return await _contest_state(connection, contest_id) == ACTIVE

    async def _conditional_transition(self, statement: Any, submission_id: UUID, parameters: dict[str, Any]) -> bool:
        with _translated_storage_errors():
            async with self._engine.begin() as connection:
                if not await self._contest_is_active(connection, submission_id):
                    return False
                result = await connection.execute(statement, {"submission_id": submission_id, **parameters})
                return result.rowcount == 1

    def _backoff_delay_seconds(self, attempt_count: int) -> float:
        """Экспоненциальная задержка с jitter — та же формула, что в processing/retry/policy.py."""
        cap = min(
            self._retry_max_delay_seconds,
            self._retry_base_delay_seconds * (2 ** max(attempt_count - 1, 0)),
        )
        return self._random.uniform(cap / 2, cap)
