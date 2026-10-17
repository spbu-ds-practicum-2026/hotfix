"""Неизменяемые объекты, которыми обмениваются репозиторий и вызывающий код."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from storage.enums import Language, SubmissionStatus, Verdict


@dataclass(frozen=True, slots=True)
class NewSubmission:
    """Данные для создания отправки. Статус всегда QUEUED, sequence = 1, fencing_token = 0."""

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
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class NewOutboxEvent:
    """payload: готовое тело события, ключи в camelCase (см. services/storage/src/storage/outbox/events.py)."""

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
