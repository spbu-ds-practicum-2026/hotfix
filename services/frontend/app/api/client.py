from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from app.api.errors import ApiError
from app.config import API_BASE_URL, USE_MOCKS
from app.models.contracts import (
    ApiErrorCode,
    Contest,
    Problem,
    StandingRow,
    StandingsResponse,
    Submission,
    SubmissionCreate,
    SubmissionStatus,
    Team,
    Verdict,
)

if TYPE_CHECKING:  
    import httpx


OPEN_CONTEST_ID = "11111111-1111-1111-1111-111111111111"
FROZEN_CONTEST_ID = "22222222-2222-2222-2222-222222222222"
EMPTY_CONTEST_ID = "33333333-3333-3333-3333-333333333333"
TIEBREAK_CONTEST_ID = "44444444-4444-4444-4444-444444444444"

MOCK_CONTESTS: dict[str, Contest] = {
    OPEN_CONTEST_ID: Contest(
        contest_id=OPEN_CONTEST_ID,
        name="Practice Round — ICPC",
        starts_at="2026-10-01T09:00:00Z",
        ends_at="2026-12-01T18:00:00Z",
        is_frozen=False,
    ),
    FROZEN_CONTEST_ID: Contest(
        contest_id=FROZEN_CONTEST_ID,
        name="Frozen Demo",
        starts_at="2026-10-01T09:00:00Z",
        ends_at="2026-12-01T18:00:00Z",
        is_frozen=True,
    ),
    EMPTY_CONTEST_ID: Contest(
        contest_id=EMPTY_CONTEST_ID,
        name="Empty Standings Demo",
        starts_at="2026-10-01T09:00:00Z",
        ends_at="2026-12-01T18:00:00Z",
        is_frozen=False,
    ),
    TIEBREAK_CONTEST_ID: Contest(
        contest_id=TIEBREAK_CONTEST_ID,
        name="Tie-Break Demo",
        starts_at="2026-10-01T09:00:00Z",
        ends_at="2026-12-01T18:00:00Z",
        is_frozen=False,
    ),
}

MOCK_TEAMS: dict[str, list[Team]] = {
    contest_id: [
        Team(
            team_id="aaaa1111-1111-1111-1111-111111111111",
            contest_id=contest_id,
            name="Team Alpha",
        ),
        Team(
            team_id="bbbb2222-2222-2222-2222-222222222222",
            contest_id=contest_id,
            name="Team Beta",
        ),
        Team(
            team_id="cccc3333-3333-3333-3333-333333333333",
            contest_id=contest_id,
            name="Team Gamma",
        ),
    ]
    for contest_id in MOCK_CONTESTS
}

MOCK_PROBLEMS: dict[str, list[Problem]] = {
    contest_id: [
        Problem(
            problem_id="aaaa0000-0000-0000-0000-000000000000",
            contest_id=contest_id,
            code="A",
            title="Hello, Verdict!",
        ),
        Problem(
            problem_id="bbbb0000-0000-0000-0000-000000000000",
            contest_id=contest_id,
            code="B",
            title="Shard Routing",
        ),
    ]
    for contest_id in MOCK_CONTESTS
}

MOCK_STANDINGS: dict[str, StandingsResponse] = {
    
    OPEN_CONTEST_ID: StandingsResponse(
        contest_id=OPEN_CONTEST_ID,
        frozen=False,
        rows=[
            StandingRow(
                rank=1,
                team_id="aaaa1111-1111-1111-1111-111111111111",
                team_name="Team Alpha",
                solved=2,
                penalty=100,
            ),
            StandingRow(
                rank=2,
                team_id="bbbb2222-2222-2222-2222-222222222222",
                team_name="Team Beta",
                solved=1,
                penalty=30,
            ),
            StandingRow(
                rank=3,
                team_id="cccc3333-3333-3333-3333-333333333333",
                team_name="Team Gamma",
                solved=0,
                penalty=0,
            ),
        ],
    ),
   
    FROZEN_CONTEST_ID: StandingsResponse(
        contest_id=FROZEN_CONTEST_ID,
        frozen=True,
        rows=[
            StandingRow(
                rank=1,
                team_id="cccc3333-3333-3333-3333-333333333333",
                team_name="Team Gamma",
                solved=1,
                penalty=50,
            ),
            StandingRow(
                rank=2,
                team_id="aaaa1111-1111-1111-1111-111111111111",
                team_name="Team Alpha",
                solved=0,
                penalty=0,
            ),
        ],
    ),

    EMPTY_CONTEST_ID: StandingsResponse(
        contest_id=EMPTY_CONTEST_ID,
        frozen=False,
        rows=[],
    ),
  
    TIEBREAK_CONTEST_ID: StandingsResponse(
        contest_id=TIEBREAK_CONTEST_ID,
        frozen=False,
        rows=[
            StandingRow(
                rank=1,
                team_id="bbbb2222-2222-2222-2222-222222222222",
                team_name="Team Beta",
                solved=2,
                penalty=70,
            ),
            StandingRow(
                rank=2,
                team_id="aaaa1111-1111-1111-1111-111111111111",
                team_name="Team Alpha",
                solved=2,
                penalty=90,
            ),
        ],
    ),
}

FINAL_STATUSES = {SubmissionStatus.COMPLETED, SubmissionStatus.SYSTEM_ERROR}


def _not_found(contest_id: str) -> ApiError:
    return ApiError(
        404, ApiErrorCode.CONTEST_NOT_FOUND, f"Contest {contest_id} not found"
    )


class ApiClient:
    def __init__(
        self,
        base_url: str = API_BASE_URL,
        use_mocks: bool = USE_MOCKS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.use_mocks = use_mocks
        self._transport = transport
       
        self.mock_submissions: dict[str, Submission] = {}

    
    def _raise_for_error(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        code = ApiErrorCode.TEMPORARILY_UNAVAILABLE
        message = response.reason_phrase or "Unexpected API error"
        try:
            body = response.json()
            error = body.get("error", {})
            code = ApiErrorCode(error.get("code", code.value))
            message = error.get("message", message)
        except (ValueError, KeyError):
            pass
        raise ApiError(response.status_code, code, message)

    async def _request(
        self, method: str, path: str, **kwargs: object
    ) -> httpx.Response:
        
        import httpx

        try:
            async with httpx.AsyncClient(
                timeout=10.0, transport=self._transport
            ) as http:
                response = await http.request(
                    method, f"{self.base_url}{path}", **kwargs
                )
        except httpx.RequestError as error:
            raise ApiError(
                503,
                ApiErrorCode.TEMPORARILY_UNAVAILABLE,
                f"API is unavailable: {error}",
            ) from error
        self._raise_for_error(response)
        return response

    
    async def create_submission(
        self, payload: SubmissionCreate, idempotency_key: str | None = None
    ) -> Submission:
        key = idempotency_key or str(uuid4())

        if self.use_mocks:
            submission = Submission(
                submission_id=str(uuid4()),
                contest_id=payload.contest_id,
                team_id=payload.team_id,
                problem_id=payload.problem_id,
                language=payload.language,
                status=SubmissionStatus.COMPLETED,
                verdict=Verdict.ACCEPTED,
                created_at="2026-10-15T12:00:00Z",
                updated_at="2026-10-15T12:00:03Z",
                sequence=3,
            )
            self.mock_submissions[submission.submission_id] = submission
            return submission

        response = await self._request(
            "POST",
            "/api/v1/submissions",
            json={
                "contestId": payload.contest_id,
                "teamId": payload.team_id,
                "problemId": payload.problem_id,
                "language": payload.language,
                "sourceCode": payload.source_code,
            },
            headers={"Idempotency-Key": key},
        )
        data = response.json()

        return Submission(
            submission_id=data["submissionId"],
            contest_id=payload.contest_id,
            team_id=payload.team_id,
            problem_id=payload.problem_id,
            language=payload.language,
            status=SubmissionStatus(data["status"]),
            verdict=None,
            created_at=data["createdAt"],
        )

    
    async def get_submission(self, submission_id: str) -> Submission:
        if self.use_mocks:
            submission = self.mock_submissions.get(submission_id)
            if submission is None:
                raise ApiError(
                    404,
                    ApiErrorCode.SUBMISSION_NOT_FOUND,
                    "Submission not found",
                )
            
            if submission.status not in FINAL_STATUSES:
                updated = submission.model_copy()
                if updated.status is SubmissionStatus.QUEUED:
                    updated = updated.model_copy(
                        update={"status": SubmissionStatus.PROCESSING}
                    )
                elif updated.status is SubmissionStatus.PROCESSING:
                    updated = updated.model_copy(
                        update={
                            "status": SubmissionStatus.COMPLETED,
                            "verdict": Verdict.ACCEPTED,
                            "updated_at": "2026-10-15T12:00:05Z",
                        }
                    )
                self.mock_submissions[submission_id] = updated
                return updated
            return submission

        response = await self._request("GET", f"/api/v1/submissions/{submission_id}")
        data = response.json()
        return Submission(
            submission_id=data["submissionId"],
            contest_id="",
            team_id="",
            problem_id="",
            language="cpp",
            status=SubmissionStatus(data["status"]),
            verdict=Verdict(data["verdict"]) if data.get("verdict") else None,
            created_at=data["createdAt"],
            updated_at=data.get("updatedAt"),
            sequence=data.get("sequence"),
        )

    
    async def list_contests(self) -> list[Contest]:
        if self.use_mocks:
            return list(MOCK_CONTESTS.values())
        response = await self._request("GET", "/api/v1/contests")
        return [
            Contest(
                contest_id=item["contestId"],
                name=item["name"],
                starts_at=item["startsAt"],
                ends_at=item["endsAt"],
                is_frozen=item.get("isFrozen", False),
            )
            for item in response.json()
        ]

    async def get_contest(self, contest_id: str) -> Contest:
        if self.use_mocks:
            contest = MOCK_CONTESTS.get(contest_id)
            if contest is None:
                raise _not_found(contest_id)
            return contest
        response = await self._request("GET", f"/api/v1/contests/{contest_id}")
        data = response.json()
        return Contest(
            contest_id=data["contestId"],
            name=data["name"],
            starts_at=data["startsAt"],
            ends_at=data["endsAt"],
            is_frozen=data.get("isFrozen", False),
        )

    async def list_teams(self, contest_id: str) -> list[Team]:
        if self.use_mocks:
            if contest_id not in MOCK_CONTESTS:
                raise _not_found(contest_id)
            return MOCK_TEAMS[contest_id]
        response = await self._request("GET", f"/api/v1/contests/{contest_id}/teams")
        return [
            Team(
                team_id=item["teamId"],
                contest_id=item["contestId"],
                name=item["name"],
            )
            for item in response.json()
        ]

    async def list_problems(self, contest_id: str) -> list[Problem]:
        if self.use_mocks:
            if contest_id not in MOCK_CONTESTS:
                raise _not_found(contest_id)
            return MOCK_PROBLEMS[contest_id]
        response = await self._request("GET", f"/api/v1/contests/{contest_id}/problems")
        return [
            Problem(
                problem_id=item["problemId"],
                contest_id=item["contestId"],
                code=item["code"],
                title=item["title"],
                time_limit_ms=item.get("timeLimitMs", 1000),
                memory_limit_kb=item.get("memoryLimitKb", 262144),
                test_config_reference=item.get("testConfigReference", ""),
            )
            for item in response.json()
        ]

    # --- standings ---

    async def get_standings(self, contest_id: str) -> StandingsResponse:
        if self.use_mocks:
            known = MOCK_STANDINGS.get(contest_id)
            if known is not None:
                return known
            # неизвестный идентификатор: базовый демо-ответ (обратная совместимость)
            return StandingsResponse(
                contest_id=contest_id,
                frozen=False,
                rows=MOCK_STANDINGS[OPEN_CONTEST_ID].rows,
            )

        response = await self._request(
            "GET", f"/api/v1/contests/{contest_id}/standings"
        )
        data = response.json()
        return StandingsResponse(
            contest_id=data["contestId"],
            frozen=data.get("frozen", False),
            rows=[
                StandingRow(
                    rank=row["rank"],
                    team_id=row["teamId"],
                    team_name=row.get("teamName", ""),
                    solved=row["solved"],
                    penalty=row["penalty"],
                )
                for row in data["rows"]
            ],
        )
