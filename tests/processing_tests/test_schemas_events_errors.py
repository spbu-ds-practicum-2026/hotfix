"""HTTP-схемы, события и формат ошибок (services/processing/src/contracts/)."""

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from contracts.errors import (
    ApiError,
    ErrorCode,
    contest_not_found,
    contest_not_open,
    idempotency_key_reused,
    problem_not_found,
    submission_not_found,
    team_not_found,
    temporarily_unavailable,
    validation_error,
)
from contracts.events import SubmissionQueuedEvent, SubmissionVerdictEvent
from contracts.schemas import (
    MAX_SOURCE_CODE_BYTES,
    SubmissionCreateRequest,
    SubmissionResponse,
    SubmissionUpdatedEvent,
)
from contracts.statuses import SubmissionStatus, Verdict
from pydantic import ValidationError

ID_1 = UUID("00000000-0000-0000-0000-000000000001")
ID_2 = UUID("00000000-0000-0000-0000-000000000002")
ID_3 = UUID("00000000-0000-0000-0000-000000000003")
NOW = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)


def _request(**overrides):
    body = {
        "contestId": str(ID_1),
        "teamId": str(ID_2),
        "problemId": str(ID_3),
        "language": "cpp",
        "sourceCode": "int main() {}",
    }
    body.update(overrides)
    return body


# ---------- SubmissionCreateRequest ----------


def test_request_accepts_camel_case_body():
    request = SubmissionCreateRequest.model_validate(_request())
    assert request.contest_id == ID_1
    assert request.language == "cpp"


def test_request_rejects_empty_source_code():
    with pytest.raises(ValidationError):
        SubmissionCreateRequest.model_validate(_request(sourceCode=""))


def test_request_source_code_limit_boundary():
    SubmissionCreateRequest.model_validate(
        _request(sourceCode="a" * MAX_SOURCE_CODE_BYTES)
    )
    with pytest.raises(ValidationError):
        SubmissionCreateRequest.model_validate(
            _request(sourceCode="a" * (MAX_SOURCE_CODE_BYTES + 1))
        )


def test_request_source_code_limit_counts_bytes_not_characters():
    # 40000 символов кириллицы = 80000 байт в UTF-8 > 65536
    with pytest.raises(ValidationError):
        SubmissionCreateRequest.model_validate(_request(sourceCode="я" * 40000))


def test_request_rejects_unsupported_language():
    with pytest.raises(ValidationError):
        SubmissionCreateRequest.model_validate(_request(language="python"))


def test_request_rejects_invalid_uuid():
    with pytest.raises(ValidationError):
        SubmissionCreateRequest.model_validate(_request(contestId="not-a-uuid"))


# ---------- SubmissionResponse / SubmissionUpdatedEvent ----------


def _response(**overrides):
    body = {
        "submissionId": str(ID_1),
        "status": "COMPLETED",
        "verdict": "ACCEPTED",
        "createdAt": NOW.isoformat(),
        "updatedAt": NOW.isoformat(),
        "sequence": 3,
    }
    body.update(overrides)
    return body


def test_response_completed_with_verdict_is_valid():
    response = SubmissionResponse.model_validate(_response())
    assert response.status is SubmissionStatus.COMPLETED
    assert response.verdict is Verdict.ACCEPTED


@pytest.mark.parametrize("status", ["QUEUED", "PROCESSING", "RETRYING", "SYSTEM_ERROR"])
def test_response_non_completed_without_verdict_is_valid(status):
    assert (
        SubmissionResponse.model_validate(
            _response(status=status, verdict=None)
        ).verdict
        is None
    )


def test_response_completed_without_verdict_is_invalid():
    with pytest.raises(ValidationError):
        SubmissionResponse.model_validate(_response(verdict=None))


def test_response_queued_with_verdict_is_invalid():
    with pytest.raises(ValidationError):
        SubmissionResponse.model_validate(_response(status="QUEUED"))


def test_updated_event_has_same_status_verdict_rule():
    body = {
        "eventId": str(ID_1),
        "submissionId": str(ID_2),
        "status": "COMPLETED",
        "verdict": None,
        "sequence": 2,
    }
    with pytest.raises(ValidationError):
        SubmissionUpdatedEvent.model_validate(body)


# ---------- Events ----------


def _queued(**overrides):
    args = {
        "event_id": ID_1,
        "trace_id": ID_2,
        "occurred_at": NOW,
        "submission_id": ID_3,
        "sequence": 1,
        "attempt_id": ID_1,
    }
    args.update(overrides)
    return SubmissionQueuedEvent(**args)


def test_queued_event_body_uses_camel_case_and_version_1():
    body = json.loads(_queued().to_message_body())
    assert body["eventId"] == str(ID_1)
    assert body["schemaVersion"] == 1
    assert body["submissionId"] == str(ID_3)
    assert body["attemptId"] == str(ID_1)
    assert body["sequence"] == 1
    assert "event_id" not in body


def test_event_sequence_must_be_positive():
    with pytest.raises(ValidationError):
        _queued(sequence=0)


def test_event_schema_version_must_be_1():
    with pytest.raises(ValidationError):
        _queued(schema_version=2)


def test_verdict_event_defaults_to_completed():
    event = SubmissionVerdictEvent(
        event_id=ID_1,
        trace_id=ID_2,
        occurred_at=NOW,
        submission_id=ID_3,
        sequence=2,
        attempt_id=ID_1,
        fencing_token=7,
        verdict=Verdict.WRONG_ANSWER,
    )
    body = json.loads(event.to_message_body())
    assert body["status"] == "COMPLETED"
    assert body["verdict"] == "WRONG_ANSWER"
    assert body["fencingToken"] == 7


def test_verdict_event_requires_verdict():
    with pytest.raises(ValidationError):
        SubmissionVerdictEvent(
            event_id=ID_1,
            trace_id=ID_2,
            occurred_at=NOW,
            submission_id=ID_3,
            sequence=2,
            attempt_id=ID_1,
            fencing_token=7,
        )


def test_verdict_event_rejects_non_completed_status():
    with pytest.raises(ValidationError):
        SubmissionVerdictEvent(
            event_id=ID_1,
            trace_id=ID_2,
            occurred_at=NOW,
            submission_id=ID_3,
            sequence=2,
            attempt_id=ID_1,
            fencing_token=7,
            status=SubmissionStatus.QUEUED,
            verdict=Verdict.ACCEPTED,
        )


# ---------- Errors ----------


@pytest.mark.parametrize(
    "factory,http_status,code",
    [
        (contest_not_found, 404, ErrorCode.CONTEST_NOT_FOUND),
        (team_not_found, 404, ErrorCode.TEAM_NOT_FOUND),
        (problem_not_found, 404, ErrorCode.PROBLEM_NOT_FOUND),
        (submission_not_found, 404, ErrorCode.SUBMISSION_NOT_FOUND),
        (idempotency_key_reused, 409, ErrorCode.IDEMPOTENCY_KEY_REUSED),
        (contest_not_open, 409, ErrorCode.CONTEST_NOT_OPEN),
        (temporarily_unavailable, 503, ErrorCode.TEMPORARILY_UNAVAILABLE),
    ],
)
def test_error_factories(factory, http_status, code):
    error = factory()
    assert isinstance(error, ApiError)
    assert error.http_status == http_status
    assert error.code is code


def test_error_body_has_single_error_format():
    body = contest_not_found().to_body()
    assert list(body) == ["error"]
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == "CONTEST_NOT_FOUND"
    assert body["error"]["details"] == {}


def test_validation_error_with_and_without_field():
    with_field = validation_error("bad value", "sourceCode")
    assert with_field.http_status == 400
    assert with_field.to_body()["error"]["details"] == {"field": "sourceCode"}
    assert validation_error("bad value").to_body()["error"]["details"] == {}
