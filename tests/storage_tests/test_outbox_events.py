"""Сборка событий Outbox (services/storage/src/storage/outbox/events.py).

Нужен sqlalchemy (pip install -r services/api/requirements.txt); без него тесты пропускаются.
"""

from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

events = pytest.importorskip("storage.outbox.events")
enums = pytest.importorskip("storage.enums")

SUBMISSION = UUID("00000000-0000-0000-0000-000000000001")
TRACE = UUID("00000000-0000-0000-0000-000000000002")
EVENT = UUID("00000000-0000-0000-0000-000000000003")
ATTEMPT = UUID("00000000-0000-0000-0000-000000000004")
NOW = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)


def test_timestamp_is_utc_with_z_suffix():
    assert events.format_utc_timestamp(NOW) == "2026-10-15T12:00:00Z"


def test_timestamp_converts_other_timezones_to_utc():
    moscow = datetime(2026, 10, 15, 15, 0, 0, tzinfo=timezone(timedelta(hours=3)))
    assert events.format_utc_timestamp(moscow) == "2026-10-15T12:00:00Z"


def test_naive_timestamp_is_rejected():
    with pytest.raises(ValueError):
        events.format_utc_timestamp(datetime(2026, 10, 15, 12, 0, 0))


def test_queued_event_contract():
    event = events.build_submission_queued_event(
        submission_id=SUBMISSION,
        trace_id=TRACE,
        occurred_at=NOW,
        event_id=EVENT,
        attempt_id=ATTEMPT,
    )
    assert event.event_type == "submission.queued"
    assert event.routing_key == "submission.queued.v1"
    assert event.schema_version == 1
    assert event.payload == {
        "eventId": str(EVENT),
        "schemaVersion": 1,
        "traceId": str(TRACE),
        "occurredAt": "2026-10-15T12:00:00Z",
        "submissionId": str(SUBMISSION),
        "attemptId": str(ATTEMPT),
        "sequence": 1,
    }


def test_queued_event_generates_ids_when_not_given():
    first = events.build_submission_queued_event(
        submission_id=SUBMISSION, trace_id=TRACE, occurred_at=NOW
    )
    second = events.build_submission_queued_event(
        submission_id=SUBMISSION, trace_id=TRACE, occurred_at=NOW
    )
    assert first.event_id != second.event_id
    assert first.payload["eventId"] == str(first.event_id)


def _verdict(**overrides):
    args = {
        "submission_id": SUBMISSION,
        "trace_id": TRACE,
        "occurred_at": NOW,
        "submitted_at": NOW - timedelta(seconds=5),
        "fencing_token": 3,
        "status": enums.SubmissionStatus.COMPLETED,
        "verdict": enums.Verdict.ACCEPTED,
        "sequence": 4,
        "event_id": EVENT,
        "attempt_id": ATTEMPT,
    }
    args.update(overrides)
    return events.build_submission_verdict_event(**args)


def test_verdict_event_contract():
    event = _verdict()
    assert event.event_type == "submission.verdict"
    assert event.routing_key == "submission.verdict.v1"
    assert event.payload["status"] == "COMPLETED"
    assert event.payload["verdict"] == "ACCEPTED"
    assert event.payload["fencingToken"] == 3
    assert event.payload["sequence"] == 4
    assert event.payload["submittedAt"] == "2026-10-15T11:59:55Z"


def test_verdict_event_requires_completed_status():
    with pytest.raises(ValueError):
        _verdict(status=enums.SubmissionStatus.SYSTEM_ERROR, verdict=None)
    with pytest.raises(ValueError):
        _verdict(status=enums.SubmissionStatus.PROCESSING)


def test_verdict_event_requires_verdict():
    with pytest.raises(ValueError):
        _verdict(verdict=None)
