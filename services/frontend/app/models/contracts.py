from enum import Enum

from pydantic import BaseModel, Field


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


class ApiErrorCode(str, Enum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    CONTEST_NOT_FOUND = "CONTEST_NOT_FOUND"
    TEAM_NOT_FOUND = "TEAM_NOT_FOUND"
    PROBLEM_NOT_FOUND = "PROBLEM_NOT_FOUND"
    SUBMISSION_NOT_FOUND = "SUBMISSION_NOT_FOUND"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    CONTEST_NOT_OPEN = "CONTEST_NOT_OPEN"
    TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"


class SubmissionCreate(BaseModel):
    contest_id: str = Field(min_length=1)
    team_id: str = Field(min_length=1)
    problem_id: str = Field(min_length=1)
    language: str = "cpp"
    source_code: str = Field(min_length=1)


class Submission(BaseModel):
    submission_id: str
    contest_id: str
    team_id: str
    problem_id: str
    language: str
    status: SubmissionStatus
    verdict: Verdict | None = None
    created_at: str
    updated_at: str | None = None
    sequence: int | None = None


class Contest(BaseModel):
    contest_id: str
    name: str
    starts_at: str
    ends_at: str
    is_frozen: bool = False


class Team(BaseModel):
    team_id: str
    contest_id: str
    name: str


class Problem(BaseModel):
    problem_id: str
    contest_id: str
    code: str
    title: str
    time_limit_ms: int = 1000
    memory_limit_kb: int = 262144
    test_config_reference: str = ""


class ProblemResult(BaseModel):
    problem_id: str
    attempts: int
    accepted: bool
    accepted_at: str | None = None
    penalty: int


class StandingRow(BaseModel):
    rank: int
    team_id: str
    team_name: str = ""
    solved: int
    penalty: int
    problems: list[ProblemResult] = Field(default_factory=list)


class StandingsResponse(BaseModel):
    contest_id: str
    frozen: bool = False
    rows: list[StandingRow]
