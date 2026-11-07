
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

idempotency = pytest.importorskip("contracts.idempotency")
errors = pytest.importorskip("contracts.errors")

from contracts.idempotency import IdempotencyDecision

CONTEST_ID = UUID("11111111-1111-1111-1111-111111111111")
TEAM_ID = UUID("aaaa1111-1111-1111-1111-111111111111")
PROBLEM_ID = UUID("aaaa0000-0000-0000-0000-000000000000")


def _now() -> datetime:
    return datetime(2026, 10, 20, 12, 0, 0, tzinfo=UTC)


class InMemorySubmissionRepository:
    """Двойник репозитория по контракту storage (tr.md, сценарий отправки)."""

    def __init__(self) -> None:
        self.submissions: dict[UUID, dict] = {}
        self.keys: dict[tuple[UUID, UUID], dict] = {}
        self.outbox: list[dict] = []
        self.contest_schedule = (
            _now() - timedelta(hours=1),
            _now() + timedelta(hours=1),
        )

    async def create_submission_with_outbox(
        self,
        submission: object,
        event: object,
        idempotency_key: UUID,
        payload_hash: str,
    ) -> dict:
        
        existing = self.keys.get((submission.team_id, idempotency_key))
        if existing is not None:
            if existing["payload_hash"] == payload_hash:
                return {"submission": self.submissions[existing["submission_id"]]}
            raise IdempotencyKeyReusedError()
     
        starts_at, ends_at = self.contest_schedule
        if not starts_at <= submission.created_at < ends_at:
            raise ContestNotOpenError()
        
        self.keys[(submission.team_id, idempotency_key)] = {
            "payload_hash": payload_hash,
            "submission_id": submission.submission_id,
        }
        self.submissions[submission.submission_id] = {
            "submission_id": submission.submission_id,
            "status": "QUEUED",
            "created_at": submission.created_at,
        }
        self.outbox.append({"submission_id": submission.submission_id})
        return {"submission": self.submissions[submission.submission_id]}


class IdempotencyKeyReusedError(Exception):
    pass


class ContestNotOpenError(Exception):
    pass


def _payload_hash(source_code: str = "int main() {}") -> str:
    return idempotency.compute_payload_hash(
        CONTEST_ID, TEAM_ID, PROBLEM_ID, "cpp", source_code
    )


class _NewSubmission:
    def __init__(self, submission_id: UUID, created_at: datetime) -> None:
        self.submission_id = submission_id
        self.team_id = TEAM_ID
        self.contest_id = CONTEST_ID
        self.created_at = created_at


@pytest.mark.asyncio
async def test_same_key_same_payload_returns_original_submission():
    repository = InMemorySubmissionRepository()
    key = uuid4()
    payload_hash = _payload_hash()
    first = await repository.create_submission_with_outbox(
        _NewSubmission(uuid4(), _now()), None, key, payload_hash
    )

    replay = await repository.create_submission_with_outbox(
        _NewSubmission(uuid4(), _now()), None, key, payload_hash
    )

    assert replay["submission"]["submission_id"] == first["submission"]["submission_id"]
    # вторая посылка не создаётся, событие outbox одно
    assert len(repository.submissions) == 1
    assert len(repository.outbox) == 1


@pytest.mark.asyncio
async def test_same_key_different_payload_conflicts():
    repository = InMemorySubmissionRepository()
    key = uuid4()
    await repository.create_submission_with_outbox(
        _NewSubmission(uuid4(), _now()), None, key, _payload_hash("int main() {}")
    )

    with pytest.raises(IdempotencyKeyReusedError):
        await repository.create_submission_with_outbox(
            _NewSubmission(uuid4(), _now()),
            None,
            key,
            _payload_hash("int main() { return 1; }"),
        )

    assert len(repository.submissions) == 1


@pytest.mark.asyncio
async def test_replay_after_contest_end_returns_original_submission():
 
    repository = InMemorySubmissionRepository()
    key = uuid4()
    payload_hash = _payload_hash()
    created = await repository.create_submission_with_outbox(
        _NewSubmission(uuid4(), _now()), None, key, payload_hash
    )

    # контест закончился
    repository.contest_schedule = (
        _now() - timedelta(hours=2),
        _now() - timedelta(hours=1),
    )
    replay = await repository.create_submission_with_outbox(
        _NewSubmission(uuid4(), _now()), None, key, payload_hash
    )

    assert (
        replay["submission"]["submission_id"] == created["submission"]["submission_id"]
    )


@pytest.mark.asyncio
async def test_new_key_after_contest_end_rejected():
    repository = InMemorySubmissionRepository()
    repository.contest_schedule = (
        _now() - timedelta(hours=2),
        _now() - timedelta(hours=1),
    )

    with pytest.raises(ContestNotOpenError):
        await repository.create_submission_with_outbox(
            _NewSubmission(uuid4(), _now()), None, uuid4(), _payload_hash()
        )


def test_key_reused_maps_to_409_idempotency_key_reused():
    error = errors.idempotency_key_reused()

    assert error.http_status == 409
    assert error.code.value == "IDEMPOTENCY_KEY_REUSED"
    assert error.to_body()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_contest_not_open_maps_to_409():
    error = errors.contest_not_open()

    assert error.http_status == 409
    assert error.code.value == "CONTEST_NOT_OPEN"


def test_decide_idempotency_contract():
    record = idempotency.IdempotencyRecord(
        payload_hash=_payload_hash(), submission_id=uuid4()
    )

    assert idempotency.decide_idempotency(None, "hash") is IdempotencyDecision.NEW
    assert (
        idempotency.decide_idempotency(record, record.payload_hash)
        is IdempotencyDecision.REPLAY
    )
    assert (
        idempotency.decide_idempotency(record, "other") is IdempotencyDecision.CONFLICT
    )
