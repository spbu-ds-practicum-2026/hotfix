from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from pydantic.alias_generators import to_camel

from contracts.statuses import SubmissionStatus, Verdict, is_valid_state

MAX_SOURCE_CODE_BYTES = 65536


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class SubmissionCreateRequest(CamelModel):
    contest_id: UUID
    team_id: UUID
    problem_id: UUID
    language: Literal["cpp"]
    source_code: str

    @field_validator("source_code")
    @classmethod
    def validate_source_code(cls, value: str) -> str:
        if not value:
            raise ValueError("sourceCode must not be empty")
        if len(value.encode("utf-8")) > MAX_SOURCE_CODE_BYTES:
            raise ValueError(f"sourceCode must be at most {MAX_SOURCE_CODE_BYTES} bytes")
        return value


class SubmissionCreateResponse(CamelModel):
    submission_id: UUID
    status: SubmissionStatus
    created_at: datetime


class SubmissionResponse(CamelModel):
    submission_id: UUID
    status: SubmissionStatus
    verdict: Verdict | None
    created_at: datetime
    updated_at: datetime
    sequence: int

    @model_validator(mode="after")
    def check_status_and_verdict(self) -> Self:
        if not is_valid_state(self.status, self.verdict):
            raise ValueError("verdict must be set if and only if status is COMPLETED")
        return self


class SubmissionUpdatedEvent(CamelModel):
    event_id: UUID
    submission_id: UUID
    status: SubmissionStatus
    verdict: Verdict | None
    sequence: int

    @model_validator(mode="after")
    def check_status_and_verdict(self) -> Self:
        if not is_valid_state(self.status, self.verdict):
            raise ValueError("verdict must be set if and only if status is COMPLETED")
        return self