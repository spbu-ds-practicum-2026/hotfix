import hashlib
from dataclasses import dataclass
from enum import Enum
from uuid import UUID


class IdempotencyDecision(str, Enum):
    NEW = "NEW"
    REPLAY = "REPLAY"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class IdempotencyRecord:
    payload_hash: str
    submission_id: UUID


def compute_payload_hash(
    contest_id: UUID,
    team_id: UUID,
    problem_id: UUID,
    language: str,
    source_code: str,
) -> str:
    canonical = "\n".join(
        [str(contest_id), str(team_id), str(problem_id), language, source_code]
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def decide_idempotency(
    record: IdempotencyRecord | None, payload_hash: str
) -> IdempotencyDecision:
    if record is None:
        return IdempotencyDecision.NEW
    if record.payload_hash == payload_hash:
        return IdempotencyDecision.REPLAY
    return IdempotencyDecision.CONFLICT
