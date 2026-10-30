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
from contracts.idempotency import compute_payload_hash
from contracts.schemas import (
    SubmissionCreateRequest,
    SubmissionCreateResponse,
    SubmissionResponse,
)
from contracts.statuses import SubmissionStatus, Verdict
from fastapi import APIRouter, Depends, Header
from storage.enums import Language
from storage.outbox.events import build_submission_queued_event
from storage.repositories import (
    DuplicateRecordError,
    IdempotencyKeyReusedError,
    NewSubmission,
    ReferenceNotFoundError,
    StorageUnavailableError,
    SubmissionRecord,
)
from storage.shard_router.router import ShardRouter

router = APIRouter(prefix="/api/v1")


def get_shard_router() -> ShardRouter:
    raise NotImplementedError("ShardRouter is not wired")


def _parse_idempotency_key(raw_key: str | None) -> UUID:
    if raw_key is None:
        raise validation_error("Idempotency-Key header is required", "Idempotency-Key")
    try:
        return UUID(raw_key)
    except ValueError:
        raise validation_error("Idempotency-Key must be a UUID", "Idempotency-Key") from None


def _to_create_response(record: SubmissionRecord) -> SubmissionCreateResponse:
    return SubmissionCreateResponse(
        submission_id=record.submission_id,
        status=SubmissionStatus(record.status.value),
        created_at=record.created_at,
    )


@router.post("/submissions", status_code=202, response_model=SubmissionCreateResponse)
async def create_submission(
    request_body: SubmissionCreateRequest,
    raw_idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    shard_router: ShardRouter = Depends(get_shard_router),  # noqa: B008
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
    submission_id = uuid4()
    trace_id = uuid4()
    try:
        contest_repository = shard_router.contest_repository_for(request_body.contest_id)
        schedule = await contest_repository.get_contest_schedule(request_body.contest_id)
        if schedule is None:
            raise contest_not_found()
        if not schedule.starts_at <= now < schedule.ends_at:
            raise contest_not_open()
        repository = shard_router.repository_for_contest(request_body.contest_id)
        result = await repository.create_submission_with_outbox(
            NewSubmission(
                submission_id=submission_id,
                contest_id=request_body.contest_id,
                team_id=request_body.team_id,
                problem_id=request_body.problem_id,
                trace_id=trace_id,
                source_code=request_body.source_code,
                created_at=now,
                language=Language.CPP,
            ),
            build_submission_queued_event(
                submission_id=submission_id, trace_id=trace_id, occurred_at=now
            ),
            idempotency_key,
            payload_hash,
        )
    except IdempotencyKeyReusedError:
        raise idempotency_key_reused() from None
    except ReferenceNotFoundError as error:
        if error.reference == "team":
            raise team_not_found() from None
        raise problem_not_found() from None
    except (StorageUnavailableError, DuplicateRecordError):
        raise temporarily_unavailable() from None
    return _to_create_response(result.submission)


@router.get("/submissions/{submission_id}", response_model=SubmissionResponse)
async def get_submission(
    submission_id: UUID,
    shard_router: ShardRouter = Depends(get_shard_router),  # noqa: B008
) -> SubmissionResponse:
    record = None
    try:
        for repository in shard_router.all_repositories():
            record = await repository.get_submission_by_id(submission_id)
            if record is not None:
                break
    except StorageUnavailableError:
        raise temporarily_unavailable() from None
    if record is None:
        raise submission_not_found()
    return SubmissionResponse(
        submission_id=record.submission_id,
        status=SubmissionStatus(record.status.value),
        verdict=Verdict(record.verdict.value) if record.verdict is not None else None,
        created_at=record.created_at,
        updated_at=record.updated_at,
        sequence=record.sequence,
    )