"""Сборка тел событий по контракту (docs/contracts/verdict-contracts.md, раздел 7). Ключи payload — camelCase."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from storage.enums import SubmissionStatus, Verdict
from storage.repositories.models import NewOutboxEvent

SCHEMA_VERSION = 1
QUEUED_EVENT_TYPE = "submission.queued"
QUEUED_ROUTING_KEY = "submission.queued.v1"
VERDICT_EVENT_TYPE = "submission.verdict"
VERDICT_ROUTING_KEY = "submission.verdict.v1"


def format_utc_timestamp(moment: datetime) -> str:
    """ISO 8601 в UTC с суффиксом Z, например 2026-10-14T10:00:00Z."""
    if moment.tzinfo is None:
        raise ValueError("Время должно быть timezone-aware (UTC)")
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_submission_queued_event(
    *,
    submission_id: UUID,
    trace_id: UUID,
    occurred_at: datetime,
    event_id: UUID | None = None,
    attempt_id: UUID | None = None,
    sequence: int = 1,
) -> NewOutboxEvent:
    event_id = event_id or uuid4()
    payload = {
        "eventId": str(event_id),
        "schemaVersion": SCHEMA_VERSION,
        "traceId": str(trace_id),
        "occurredAt": format_utc_timestamp(occurred_at),
        "submissionId": str(submission_id),
        "attemptId": str(attempt_id or uuid4()),
        "sequence": sequence,
    }
    return NewOutboxEvent(
        event_id=event_id,
        submission_id=submission_id,
        trace_id=trace_id,
        event_type=QUEUED_EVENT_TYPE,
        routing_key=QUEUED_ROUTING_KEY,
        payload=payload,
        created_at=occurred_at,
        schema_version=SCHEMA_VERSION,
    )


def build_submission_verdict_event(
    *,
    submission_id: UUID,
    trace_id: UUID,
    occurred_at: datetime,
    submitted_at: datetime,
    fencing_token: int,
    status: SubmissionStatus,
    verdict: Verdict | None,
    sequence: int,
    event_id: UUID | None = None,
    attempt_id: UUID | None = None,
) -> NewOutboxEvent:
    """submittedAt добавлен по требованию icpc-scoring-v2: без него нельзя посчитать штраф.

    Как и модель SubmissionVerdictEvent в services/processing/src/contracts/events.py, событие бывает
    только для status = COMPLETED и обязано содержать вердикт.
    """
    if status is not SubmissionStatus.COMPLETED or verdict is None:
        raise ValueError("submission.verdict.v1 публикуется только для COMPLETED с вердиктом")
    event_id = event_id or uuid4()
    payload = {
        "eventId": str(event_id),
        "schemaVersion": SCHEMA_VERSION,
        "traceId": str(trace_id),
        "occurredAt": format_utc_timestamp(occurred_at),
        "submissionId": str(submission_id),
        "attemptId": str(attempt_id or uuid4()),
        "fencingToken": fencing_token,
        "status": status.value,
        "verdict": verdict.value,
        "sequence": sequence,
        "submittedAt": format_utc_timestamp(submitted_at),
    }
    return NewOutboxEvent(
        event_id=event_id,
        submission_id=submission_id,
        trace_id=trace_id,
        event_type=VERDICT_EVENT_TYPE,
        routing_key=VERDICT_ROUTING_KEY,
        payload=payload,
        created_at=occurred_at,
        schema_version=SCHEMA_VERSION,
    )
