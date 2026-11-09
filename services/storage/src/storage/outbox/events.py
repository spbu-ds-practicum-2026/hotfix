"""Сборка тел событий по docs/tr.md, раздел «Формат событий». Ключи payload — camelCase.

submission.queued.v1:  eventId, schemaVersion, traceId, occurredAt, submissionId, sequence.
submission.verdict.v1: те же поля + fencingToken, status (всегда COMPLETED), verdict, submittedAt,
                       contestId, teamId, problemId.
attemptId в событиях нет: попытка появляется только после захвата посылки worker'ом.
"""

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
    sequence: int = 1,
) -> NewOutboxEvent:
    event_id = event_id or uuid4()
    payload = {
        "eventId": str(event_id),
        "schemaVersion": SCHEMA_VERSION,
        "traceId": str(trace_id),
        "occurredAt": format_utc_timestamp(occurred_at),
        "submissionId": str(submission_id),
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
    contest_id: UUID,
    team_id: UUID,
    problem_id: UUID,
    trace_id: UUID,
    occurred_at: datetime,
    submitted_at: datetime,
    fencing_token: int,
    status: SubmissionStatus,
    verdict: Verdict | None,
    sequence: int,
    event_id: UUID | None = None,
) -> NewOutboxEvent:
    """Событие бывает только для status = COMPLETED и обязано содержать вердикт (при SYSTEM_ERROR события нет)."""
    if status is not SubmissionStatus.COMPLETED or verdict is None:
        raise ValueError("submission.verdict.v1 публикуется только для COMPLETED с вердиктом")
    event_id = event_id or uuid4()
    payload = {
        "eventId": str(event_id),
        "schemaVersion": SCHEMA_VERSION,
        "traceId": str(trace_id),
        "occurredAt": format_utc_timestamp(occurred_at),
        "submissionId": str(submission_id),
        "sequence": sequence,
        "fencingToken": fencing_token,
        "status": status.value,
        "verdict": verdict.value,
        "submittedAt": format_utc_timestamp(submitted_at),
        "contestId": str(contest_id),
        "teamId": str(team_id),
        "problemId": str(problem_id),
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
