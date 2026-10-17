from enum import Enum


class SubmissionStatus(str, Enum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    RETRYING = "RETRYING"
    COMPLETED = "COMPLETED"
    SYSTEM_ERROR = "SYSTEM_ERROR"


class Verdict(str, Enum):
    ACCEPTED = "ACCEPTED"
    WRONG_ANSWER = "WRONG_ANSWER"
    COMPILATION_ERROR = "COMPILATION_ERROR"
    TIME_LIMIT_EXCEEDED = "TIME_LIMIT_EXCEEDED"
    RUNTIME_ERROR = "RUNTIME_ERROR"


FINAL_STATUSES = frozenset({SubmissionStatus.COMPLETED, SubmissionStatus.SYSTEM_ERROR})

ALLOWED_TRANSITIONS: dict[SubmissionStatus, frozenset[SubmissionStatus]] = {
    SubmissionStatus.QUEUED: frozenset({SubmissionStatus.PROCESSING}),
    SubmissionStatus.PROCESSING: frozenset(
        {
            SubmissionStatus.COMPLETED,
            SubmissionStatus.RETRYING,
            SubmissionStatus.SYSTEM_ERROR,
        }
    ),
    SubmissionStatus.RETRYING: frozenset(
        {
            SubmissionStatus.QUEUED,
            SubmissionStatus.PROCESSING,
            SubmissionStatus.SYSTEM_ERROR,
        }
    ),
    SubmissionStatus.COMPLETED: frozenset(),
    SubmissionStatus.SYSTEM_ERROR: frozenset(),
}


def is_final(status: SubmissionStatus) -> bool:
    return status in FINAL_STATUSES


def can_transition(current: SubmissionStatus, target: SubmissionStatus) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def is_valid_state(status: SubmissionStatus, verdict: Verdict | None) -> bool:
    return (verdict is not None) == (status is SubmissionStatus.COMPLETED)