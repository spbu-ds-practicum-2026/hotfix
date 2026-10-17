from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from contracts.statuses import Verdict


class JudgeStatus(str, Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    WRONG_ANSWER = "WRONG_ANSWER"
    COMPILATION_ERROR = "COMPILATION_ERROR"
    TIME_LIMIT_EXCEEDED = "TIME_LIMIT_EXCEEDED"
    RUNTIME_ERROR = "RUNTIME_ERROR"


@dataclass(frozen=True)
class JudgeResult:
    status: JudgeStatus
    stdout: str | None = None
    stderr: str | None = None
    time_ms: int | None = None
    memory_kb: int | None = None


class JudgeError(Exception):
    pass


class JudgeUnavailableError(JudgeError):
    pass


class JudgeTimeoutError(JudgeError):
    pass


class JudgeProtocolError(JudgeError):
    pass


class Judge0Adapter(Protocol):
    async def submit(
        self,
        source_code: str,
        language: str,
        time_limit_ms: int,
        memory_limit_kb: int,
    ) -> str: ...

    async def get_result(self, judge_submission_id: str) -> JudgeResult: ...


_VERDICT_BY_JUDGE_STATUS: dict[JudgeStatus, Verdict] = {
    JudgeStatus.ACCEPTED: Verdict.ACCEPTED,
    JudgeStatus.WRONG_ANSWER: Verdict.WRONG_ANSWER,
    JudgeStatus.COMPILATION_ERROR: Verdict.COMPILATION_ERROR,
    JudgeStatus.TIME_LIMIT_EXCEEDED: Verdict.TIME_LIMIT_EXCEEDED,
    JudgeStatus.RUNTIME_ERROR: Verdict.RUNTIME_ERROR,
}


def to_verdict(status: JudgeStatus) -> Verdict | None:
    return _VERDICT_BY_JUDGE_STATUS.get(status)


def map_judge0_status_id(status_id: int) -> JudgeStatus:
    if status_id in (1, 2):
        return JudgeStatus.PENDING
    if status_id == 3:
        return JudgeStatus.ACCEPTED
    if status_id == 4:
        return JudgeStatus.WRONG_ANSWER
    if status_id == 5:
        return JudgeStatus.TIME_LIMIT_EXCEEDED
    if status_id == 6:
        return JudgeStatus.COMPILATION_ERROR
    if 7 <= status_id <= 12:
        return JudgeStatus.RUNTIME_ERROR
    raise JudgeProtocolError(f"unexpected Judge0 status id {status_id}")
