from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from contracts.schemas import CamelModel
from contracts.statuses import SubmissionStatus, Verdict


class EventBase(CamelModel):
    event_id: UUID
    schema_version: Literal[1] = 1
    trace_id: UUID
    occurred_at: datetime
    submission_id: UUID
    sequence: int = Field(ge=1)

    def to_message_body(self) -> str:
        return self.model_dump_json(by_alias=True)


class SubmissionQueuedEvent(EventBase):

    attempt_id: UUID


class SubmissionVerdictEvent(EventBase):

    attempt_id: UUID
    fencing_token: int
    status: Literal[SubmissionStatus.COMPLETED] = SubmissionStatus.COMPLETED
    verdict: Verdict