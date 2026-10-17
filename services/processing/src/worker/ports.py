from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol
from uuid import UUID

from contracts.statuses import SubmissionStatus, Verdict


class StorageUnavailableError(Exception):
    pass


class IdempotencyKeyReusedError(Exception):
    pass


@dataclass(frozen=True)
class SubmissionState:
    status: SubmissionStatus
    verdict: Verdict | None
    sequence: int


@dataclass(frozen=True)
class SubmissionRecord:
    submission_id: UUID
    state: SubmissionState
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class OutboxEvent:
    event_id: UUID
    submission_id: UUID
    routing_key: str
    body: str
    attempts: int = 0


@dataclass(frozen=True)
class NewSubmission:
    submission_id: UUID
    contest_id: UUID
    team_id: UUID
    problem_id: UUID
    language: str
    source_code: str
    created_at: datetime


@dataclass(frozen=True)
class CreateSubmissionResult:
    submission: SubmissionRecord
    is_new: bool


@dataclass(frozen=True)
class Claim:
    submission_id: UUID
    attempt_id: UUID
    attempt_count: int
    fencing_token: int


class WriteResultOutcome(str, Enum):
    SUCCESS = "success"
    STALE_TOKEN = "stale_token"


@dataclass(frozen=True)
class JudgingData:
    source_code: str
    language: str
    time_limit_ms: int
    memory_limit_kb: int


@dataclass(frozen=True)
class AffectedSubmission:
    submission_id: UUID
    status: SubmissionStatus


class TargetCheck(str, Enum):
    OK = "OK"
    CONTEST_NOT_FOUND = "CONTEST_NOT_FOUND"
    TEAM_NOT_FOUND = "TEAM_NOT_FOUND"
    PROBLEM_NOT_FOUND = "PROBLEM_NOT_FOUND"
    CONTEST_NOT_OPEN = "CONTEST_NOT_OPEN"


class TargetChecker(Protocol):
    async def check(
        self, contest_id: UUID, team_id: UUID, problem_id: UUID, now: datetime
    ) -> TargetCheck: ...


class SubmissionStore(Protocol):
    async def create_submission_with_outbox(
        self,
        submission: NewSubmission,
        idempotency_key: UUID,
        payload_hash: str,
        queued_event: OutboxEvent,
    ) -> CreateSubmissionResult: ...

    async def get_submission_by_id(
        self, submission_id: UUID
    ) -> SubmissionRecord | None: ...


class OutboxRepository(Protocol):
    async def get_pending_outbox_events(self, limit: int) -> list[OutboxEvent]: ...

    async def mark_outbox_event_published(self, event_id: UUID) -> None: ...

    async def record_outbox_publish_failure(
        self, event_id: UUID, error: str
    ) -> None: ...


class WorkerStore(Protocol):
    async def claim(
        self, submission_id: UUID, worker_id: str, lease_seconds: int
    ) -> Claim | None: ...

    async def extend_lease(
        self, submission_id: UUID, fencing_token: int, lease_seconds: int
    ) -> bool: ...

    async def write_result(
        self,
        submission_id: UUID,
        fencing_token: int,
        status: SubmissionStatus,
        verdict: Verdict | None,
        verdict_event: OutboxEvent,
    ) -> WriteResultOutcome: ...

    async def mark_retrying(
        self, submission_id: UUID, fencing_token: int, reason: str
    ) -> bool: ...

    async def mark_system_error(
        self, submission_id: UUID, fencing_token: int, reason: str
    ) -> bool: ...

    async def recover_expired(self, limit: int) -> list[AffectedSubmission]: ...

    async def requeue_retrying(self, limit: int) -> int: ...

    async def load_for_judging(self, submission_id: UUID) -> JudgingData | None: ...


class MessagePublisher(Protocol):
    async def publish_with_confirm(
        self,
        routing_key: str,
        message_id: UUID,
        body: str,
        timeout_seconds: float,
    ) -> bool: ...


@dataclass(frozen=True)
class DeadLetter:
    message_id: UUID
    body: str
    reason: str
    submission_id: UUID | None
    attempts: int


class DeadLetterSink(Protocol):
    async def send(self, dead_letter: DeadLetter) -> None: ...


@dataclass(frozen=True)
class Delivery:
    message_id: UUID
    body: str
    is_redelivered: bool = False


class AckDecision(str, Enum):
    ACK = "ACK"
    REQUEUE_WITH_DELAY = "REQUEUE_WITH_DELAY"
    DEAD_LETTER = "DEAD_LETTER"


class MessageHandler(Protocol):
    async def handle(self, delivery: Delivery) -> AckDecision: ...
