from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID


class Verdict(str, Enum):
    ACCEPTED = "ACCEPTED"
    WRONG_ANSWER = "WRONG_ANSWER"
    TIME_LIMIT_EXCEEDED = "TIME_LIMIT_EXCEEDED"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    COMPILATION_ERROR = "COMPILATION_ERROR"
    SYSTEM_ERROR = "SYSTEM_ERROR"

    @property
    def is_accepted(self) -> bool:
        return self == Verdict.ACCEPTED

    @property
    def is_penalized(self) -> bool:
        return self in (
            Verdict.WRONG_ANSWER,
            Verdict.TIME_LIMIT_EXCEEDED,
            Verdict.RUNTIME_ERROR,
        )

    @property
    def is_ignored_in_penalty(self) -> bool:
        return self in (Verdict.COMPILATION_ERROR, Verdict.SYSTEM_ERROR)


class SubmissionStatus(str, Enum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    RETRYING = "RETRYING"
    COMPLETED = "COMPLETED"
    SYSTEM_ERROR = "SYSTEM_ERROR"


@dataclass(frozen=True, slots=True)
class Contest:
    contest_id: UUID
    name: str
    starts_at: datetime
    ends_at: datetime
    is_frozen: bool = False

    def is_active(self, now: datetime) -> bool:
        return self.starts_at <= now < self.ends_at

    def is_closed(self, now: datetime) -> bool:
        return now >= self.ends_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "contestId": str(self.contest_id),
            "name": self.name,
            "startsAt": self.starts_at.isoformat().replace("+00:00", "Z"),
            "endsAt": self.ends_at.isoformat().replace("+00:00", "Z"),
            "isFrozen": self.is_frozen,
        }


@dataclass(frozen=True, slots=True)
class Team:
    team_id: UUID
    contest_id: UUID
    name: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "teamId": str(self.team_id),
            "contestId": str(self.contest_id),
            "name": self.name,
        }


@dataclass(frozen=True, slots=True)
class Problem:
    problem_id: UUID
    contest_id: UUID
    code: str
    title: str
    time_limit_ms: int = 2000
    memory_limit_kb: int = 262144
    test_config_reference: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "problemId": str(self.problem_id),
            "contestId": str(self.contest_id),
            "code": self.code,
            "title": self.title,
            "timeLimitMs": self.time_limit_ms,
            "memoryLimitKb": self.memory_limit_kb,
            "testConfigReference": self.test_config_reference,
        }


@dataclass(frozen=True, slots=True)
class SubmissionAttempt:
    submission_id: UUID
    contest_id: UUID
    team_id: UUID
    problem_id: UUID
    status: SubmissionStatus
    verdict: Verdict | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ProblemResult:
    problem_id: UUID
    attempts: int
    accepted: bool
    accepted_at: datetime | None = None
    penalty: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "problemId": str(self.problem_id),
            "attempts": self.attempts,
            "accepted": self.accepted,
            "acceptedAt": (
                self.accepted_at.isoformat().replace("+00:00", "Z")
                if self.accepted_at is not None
                else None
            ),
            "penalty": self.penalty,
        }


@dataclass(frozen=True, slots=True)
class StandingRow:
    rank: int
    team_id: UUID
    solved: int
    penalty: int
    problems: list[ProblemResult] = field(default_factory=list)
    team_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {
            "rank": self.rank,
            "teamId": str(self.team_id),
            "solved": self.solved,
            "penalty": self.penalty,
            "problems": [p.to_dict() for p in self.problems],
        }
        if self.team_name is not None:
            res["teamName"] = self.team_name
        return res


@dataclass(frozen=True, slots=True)
class Standings:
    contest_id: UUID
    frozen: bool
    rows: list[StandingRow]
    last_event_sequence: int = 0
    updated_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "contestId": str(self.contest_id),
            "frozen": self.frozen,
            "rows": [row.to_dict() for row in self.rows],
        }
