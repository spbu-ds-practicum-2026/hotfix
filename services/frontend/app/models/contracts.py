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


class StandingRow(BaseModel):
    rank: int
    team_id: str
    team_name: str
    solved: int
    penalty: int


class StandingsResponse(BaseModel):
    contest_id: str
    rows: list[StandingRow]
