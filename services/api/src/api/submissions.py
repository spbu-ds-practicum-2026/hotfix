"""POST /api/v1/submissions и GET /api/v1/submissions/{submission_id}.

Порядок проверок POST фиксирован:
1) поля и заголовок; 2) ключ идемпотентности; 3) сущности; 4) окно соревнования;
5) одна транзакция «submission + ключ + Outbox».
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from contracts.errors import (
    contest_not_found,
    contest_not_open,
    idempotency_key_reused,
    problem_not_found,
    submission_not_found,
    team_not_found,
    temporarily_unavailable,
    validation_error,
)
from contracts.events import SubmissionQueuedEvent
from contracts.idempotency import compute_payload_hash
from contracts.schemas import (
    SubmissionCreateRequest,
    SubmissionCreateResponse,
    SubmissionResponse,
)
from fastapi import APIRouter, Depends, Header
from rabbitmq.names import QUEUED_ROUTING_KEY
from worker.ports import (
    CreateSubmissionResult,
    IdempotencyKeyReusedError,
    NewSubmission,
    OutboxEvent,
    StorageUnavailableError,
    SubmissionRecord,
    SubmissionStore,
    TargetCheck,
    TargetChecker,
)

router = APIRouter(prefix="/api/v1")


def get_submission_store() -> SubmissionStore:
    raise NotImplementedError("SubmissionStore is not wired")


def get_target_checker() -> TargetChecker:
    raise NotImplementedError("TargetChecker is not wired")


_TARGET_ERRORS = {
    TargetCheck.CONTEST_NOT_FOUND: contest_not_found,
    TargetCheck.TEAM_NOT_FOUND: team_not_found,
    TargetCheck.PROBLEM_NOT_FOUND: problem_not_found,
    TargetCheck.CONTEST_NOT_OPEN: contest_not_open,
}


def _parse_idempotency_key(raw_key: str | None) -> UUID:
    if raw_key is None:
        raise validation_error(
            "Idempotency-Key header is required",
            "Idempotency-Key",
        )

    try:
        return UUID(raw_key)
    except ValueError:
        raise validation_error(
            "Idempotency-Key must be a UUID",
            "Idempotency-Key",
        ) from None


def _to_create_response(record: SubmissionRecord) -> SubmissionCreateResponse:
    return SubmissionCreateResponse(
        submission_id=record.submission_id,
        status=record.state.status,
        created_at=record.created_at,
    )


@router.post(
    "/submissions",
    status_code=202,
    response_model=SubmissionCreateResponse,
)
async def create_submission(
    request_body: SubmissionCreateRequest,
    raw_idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
    ),
    submission_store: SubmissionStore = Depends(  # noqa: B008
        get_submission_store
    ),
    target_checker: TargetChecker = Depends(get_target_checker),  # noqa: B008
) -> SubmissionCreateResponse:
    idempotency_key = _parse_idempotency_key(raw_idempotency_key)

    payload_hash = compute_payload_hash(
        request_body.contest_id,
        request_body.team_id,
        request_body.problem_id,
        request_body.language,
        request_body.source_code,
    )

    now = datetime.now(UTC)

    try:
        target_check = await target_checker.check(
            request_body.contest_id,
            request_body.team_id,
            request_body.problem_id,
            now,
        )

        if target_check is not TargetCheck.OK:
            raise _TARGET_ERRORS[target_check]()

        submission_id = uuid4()

        queued_event = SubmissionQueuedEvent(
            event_id=uuid4(),
            trace_id=uuid4(),
            occurred_at=now,
            submission_id=submission_id,
            attempt_id=uuid4(),
            sequence=1,
        )

        result: CreateSubmissionResult = (
            await submission_store.create_submission_with_outbox(
                NewSubmission(
                    submission_id=submission_id,
                    contest_id=request_body.contest_id,
                    team_id=request_body.team_id,
                    problem_id=request_body.problem_id,
                    language=request_body.language,
                    source_code=request_body.source_code,
                    created_at=now,
                ),
                idempotency_key,
                payload_hash,
                OutboxEvent(
                    event_id=queued_event.event_id,
                    submission_id=submission_id,
                    routing_key=QUEUED_ROUTING_KEY,
                    body=queued_event.to_message_body(),
                ),
            )
        )

    except IdempotencyKeyReusedError:
        raise idempotency_key_reused() from None
    except StorageUnavailableError:
        raise temporarily_unavailable() from None

    return _to_create_response(result.submission)


@router.get(
    "/submissions/{submission_id}",
    response_model=SubmissionResponse,
)
async def get_submission(
    submission_id: UUID,
    submission_store: SubmissionStore = Depends(  # noqa: B008
        get_submission_store
    ),
) -> SubmissionResponse:
    try:
        record = await submission_store.get_submission_by_id(submission_id)
    except StorageUnavailableError:
        raise temporarily_unavailable() from None

    if record is None:
        raise submission_not_found()

    return SubmissionResponse(
        submission_id=record.submission_id,
        status=record.state.status,
        verdict=record.state.verdict,
        created_at=record.created_at,
        updated_at=record.updated_at,
        sequence=record.state.sequence,
    )