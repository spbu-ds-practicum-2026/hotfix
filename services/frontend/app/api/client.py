from __future__ import annotations

from uuid import uuid4

import httpx
from app.config import API_BASE_URL, USE_MOCKS
from app.models.contracts import (
    StandingRow,
    StandingsResponse,
    Submission,
    SubmissionCreate,
    SubmissionStatus,
    Verdict,
)


class ApiClient:
    def __init__(self, base_url: str = API_BASE_URL, use_mocks: bool = USE_MOCKS):
        self.base_url = base_url.rstrip("/")
        self.use_mocks = use_mocks

    async def create_submission(
        self, payload: SubmissionCreate, idempotency_key: str | None = None
    ) -> Submission:
        key = idempotency_key or str(uuid4())

        if self.use_mocks:
            return Submission(
                submission_id="mock-submission-001",
                contest_id=payload.contest_id,
                team_id=payload.team_id,
                problem_id=payload.problem_id,
                language=payload.language,
                status=SubmissionStatus.COMPLETED,
                verdict=Verdict.ACCEPTED,
                created_at="2026-10-15T12:00:00Z",
                updated_at="2026-10-15T12:00:03Z",
            )

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{self.base_url}/api/v1/submissions",
                json={
                    "contestId": payload.contest_id,
                    "teamId": payload.team_id,
                    "problemId": payload.problem_id,
                    "language": payload.language,
                    "sourceCode": payload.source_code,
                },
                headers={"Idempotency-Key": key},
            )
            response.raise_for_status()
            data = response.json()

        return Submission.model_validate(
            {
                "submission_id": data["submissionId"],
                "contest_id": data["contestId"],
                "team_id": data["teamId"],
                "problem_id": data["problemId"],
                "language": data["language"],
                "status": data["status"],
                "verdict": data.get("verdict"),
                "created_at": data["createdAt"],
                "updated_at": data.get("updatedAt"),
            }
        )

    async def get_standings(self, contest_id: str) -> StandingsResponse:
        if self.use_mocks:
            return StandingsResponse(
                contest_id=contest_id,
                rows=[
                    StandingRow(
                        rank=1,
                        team_id="team-01",
                        team_name="Team Alpha",
                        solved=5,
                        penalty=421,
                    ),
                    StandingRow(
                        rank=2,
                        team_id="team-02",
                        team_name="Team Beta",
                        solved=4,
                        penalty=503,
                    ),
                    StandingRow(
                        rank=3,
                        team_id="team-03",
                        team_name="Team Gamma",
                        solved=3,
                        penalty=617,
                    ),
                ],
            )

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{self.base_url}/api/v1/contests/{contest_id}/standings"
            )
            response.raise_for_status()
            data = response.json()

        return StandingsResponse(
            contest_id=data["contestId"],
            rows=[
                StandingRow(
                    rank=row["rank"],
                    team_id=row["teamId"],
                    team_name=row["teamName"],
                    solved=row["solved"],
                    penalty=row["penalty"],
                )
                for row in data["rows"]
            ],
        )
