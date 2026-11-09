from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol
from uuid import UUID

from contracts.statuses import SubmissionStatus, Verdict


class BrokerUnavailableError(Exception):
    pass


class SubmissionNotFoundError(Exception):
    pass


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


class WorkerStore(Protocol):
    async def claim_submission(
        self, submission_id: UUID, worker_id: UUID, lease_seconds: int
    ) -> Claim | None: ...

    async def extend_lease(
        self, submission_id: UUID, fencing_token: int, lease_seconds: int
    ) -> bool: ...

    async def write_result(
        self, submission_id: UUID, fencing_token: int, verdict: Verdict
    ) -> WriteResultOutcome: ...

    async def mark_retrying(
        self,
        submission_id: UUID,
        fencing_token: int,
        reason: str,
        next_attempt_at: datetime,
    ) -> bool: ...

    async def mark_system_error(
        self, submission_id: UUID, fencing_token: int, reason: str
    ) -> bool: ...

    async def recover_expired(
        self, limit: int, max_attempts: int
    ) -> list[AffectedSubmission]: ...

    async def enqueue_due_retries(self, limit: int) -> int: ...

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