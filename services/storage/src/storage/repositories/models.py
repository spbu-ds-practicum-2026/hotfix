"""Неизменяемые объекты, которыми обмениваются репозиторий и вызывающий код."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from storage.enums import Language, SubmissionStatus, Verdict

_ATTEMPT_NAMESPACE = uuid5(NAMESPACE_URL, "https://verdict.local/attempt")


def attempt_id_for(submission_id: UUID, fencing_token: int) -> UUID:
    """attemptId для логов worker'а. Вычисляется из (submissionId, fencingToken), в БД и событиях не хранится."""
    return uuid5(_ATTEMPT_NAMESPACE, f"{submission_id}:{fencing_token}")


class WriteResultOutcome(str, Enum):
    SUCCESS = "success"
    STALE_TOKEN = "stale_token"


@dataclass(frozen=True, slots=True)
class NewSubmission:
    """Данные для создания отправки. Статус всегда QUEUED, sequence = 1, fencing_token = 0.

    created_at одновременно становится submitted_at (время принятия отправки, для расчёта штрафа ICPC).
    """

    submission_id: UUID
    contest_id: UUID
    team_id: UUID
    problem_id: UUID
    trace_id: UUID
    source_code: str
    created_at: datetime  # timezone-aware, UTC
    language: Language = Language.CPP


@dataclass(frozen=True, slots=True)
class SubmissionRecord:
    submission_id: UUID
    contest_id: UUID
    team_id: UUID
    problem_id: UUID
    trace_id: UUID
    source_code: str
    language: Language
    status: SubmissionStatus
    verdict: Verdict | None
    sequence: int
    fencing_token: int
    attempt_count: int
    lease_until: datetime | None
    worker_id: UUID | None
    next_attempt_at: datetime | None
    last_error: str | None
    submitted_at: datetime
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class NewOutboxEvent:
    """payload: готовое тело события, ключи в camelCase (см. storage/outbox/events.py)."""

    event_id: UUID
    submission_id: UUID
    trace_id: UUID
    event_type: str  # "submission.queued" / "submission.verdict"
    routing_key: str  # "submission.queued.v1" / "submission.verdict.v1"
    payload: dict[str, Any]
    created_at: datetime
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class OutboxEventRecord:
    event_id: UUID
    submission_id: UUID
    trace_id: UUID
    event_type: str
    routing_key: str
    schema_version: int
    payload: dict[str, Any]
    created_at: datetime
    published_at: datetime | None
    attempt_count: int
    last_error: str | None


@dataclass(frozen=True, slots=True)
class CreateSubmissionResult:
    """is_new = False означает идемпотентный повтор: возвращена уже существующая отправка, ничего не записано."""

    submission: SubmissionRecord
    is_new: bool


@dataclass(frozen=True, slots=True)
class ClaimedSubmission:
    """Результат успешного захвата. attempt_count — номер этой попытки (1 для первой)."""

    submission_id: UUID
    attempt_id: UUID
    attempt_count: int
    fencing_token: int
    lease_until: datetime


@dataclass(frozen=True, slots=True)
class RecoveredSubmission:
    """Что сделал Watchdog с посылкой: RETRYING или SYSTEM_ERROR."""

    submission_id: UUID
    status: SubmissionStatus


@dataclass(frozen=True, slots=True)
class JudgingData:
    source_code: str
    language: Language
    time_limit_ms: int
    memory_limit_kb: int
    test_config_reference: str


@dataclass(frozen=True, slots=True)
class ContestSchedule:
    contest_id: UUID
    name: str
    starts_at: datetime
    ends_at: datetime
    is_frozen: bool
    migration_state: str  # "ACTIVE" | "MIGRATING"
