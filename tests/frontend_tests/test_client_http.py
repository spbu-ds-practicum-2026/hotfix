
import json
from uuid import UUID

import pytest

client_module = pytest.importorskip("app.api.client")
errors_module = pytest.importorskip("app.api.errors")
httpx = pytest.importorskip("httpx")

from app.api.errors import ApiError
from app.models.contracts import ApiErrorCode, SubmissionStatus

BASE_URL = "http://api.test"


def _api(transport: httpx.MockTransport) -> client_module.ApiClient:
    return client_module.ApiClient(
        base_url=BASE_URL, use_mocks=False, transport=transport
    )


def _error_body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message, "details": {}}}


@pytest.mark.asyncio
async def test_create_submission_sends_uuid_key_and_camel_case_body():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            202,
            json={
                "submissionId": "3f1c2b9e-5a7d-4c1e-9b0a-1d2e3f4a5b6c",
                "status": "QUEUED",
                "createdAt": "2026-10-12T10:00:00Z",
            },
        )

    api = _api(httpx.MockTransport(handler))

    submission = await api.create_submission(
        client_module.SubmissionCreate(
            contest_id="11111111-1111-1111-1111-111111111111",
            team_id="aaaa1111-1111-1111-1111-111111111111",
            problem_id="aaaa0000-0000-0000-0000-000000000000",
            language="cpp",
            source_code="int main() {}",
        ),
        idempotency_key="0f14d0ab-9605-4a62-a9e4-5ed26688389b",
    )

    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == f"{BASE_URL}/api/v1/submissions"
    # Idempotency-Key — UUID
    UUID(str(request.headers["Idempotency-Key"]))
    # тело — camelCase по контракту
    body = json.loads(request.content.decode())
    assert set(body) == {"contestId", "teamId", "problemId", "language", "sourceCode"}
    assert body["language"] == "cpp"

    assert submission.submission_id == "3f1c2b9e-5a7d-4c1e-9b0a-1d2e3f4a5b6c"
    assert submission.status is SubmissionStatus.QUEUED


@pytest.mark.asyncio
async def test_submit_replay_with_same_key_returns_same_submission():
    """Повтор submit: тот же ключ и то же тело → прежняя посылка (202)."""
    stored = {}

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.headers["Idempotency-Key"]
        if key in stored:
            return httpx.Response(
                202,
                json={
                    "submissionId": stored[key],
                    "status": "PROCESSING",
                    "createdAt": "2026-10-12T10:00:00Z",
                },
            )
        stored[key] = "3f1c2b9e-5a7d-4c1e-9b0a-1d2e3f4a5b6c"
        return httpx.Response(
            202,
            json={
                "submissionId": stored[key],
                "status": "QUEUED",
                "createdAt": "2026-10-12T10:00:00Z",
            },
        )

    api = _api(httpx.MockTransport(handler))
    payload = client_module.SubmissionCreate(
        contest_id="11111111-1111-1111-1111-111111111111",
        team_id="aaaa1111-1111-1111-1111-111111111111",
        problem_id="aaaa0000-0000-0000-0000-000000000000",
        language="cpp",
        source_code="int main() {}",
    )
    key = "0f14d0ab-9605-4a62-a9e4-5ed26688389b"

    first = await api.create_submission(payload, idempotency_key=key)
    replay = await api.create_submission(payload, idempotency_key=key)

    assert replay.submission_id == first.submission_id
    # статус посылки может уже измениться, id тот же
    assert replay.status is SubmissionStatus.PROCESSING


@pytest.mark.asyncio
async def test_submit_key_conflict_raises_409_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json=_error_body(
                "IDEMPOTENCY_KEY_REUSED",
                "Idempotency-Key was already used with a different payload",
            ),
        )

    api = _api(httpx.MockTransport(handler))

    with pytest.raises(ApiError) as exc_info:
        await api.create_submission(
            client_module.SubmissionCreate(
                contest_id="11111111-1111-1111-1111-111111111111",
                team_id="aaaa1111-1111-1111-1111-111111111111",
                problem_id="aaaa0000-0000-0000-0000-000000000000",
                language="cpp",
                source_code="int main() {}",
            ),
            idempotency_key="0f14d0ab-9605-4a62-a9e4-5ed26688389b",
        )

    assert exc_info.value.http_status == 409
    assert exc_info.value.code is ApiErrorCode.IDEMPOTENCY_KEY_REUSED


@pytest.mark.asyncio
async def test_submit_contest_closed_raises_409_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json=_error_body("CONTEST_NOT_OPEN", "Contest is not open for submissions"),
        )

    api = _api(httpx.MockTransport(handler))

    with pytest.raises(ApiError) as exc_info:
        await api.create_submission(
            client_module.SubmissionCreate(
                contest_id="11111111-1111-1111-1111-111111111111",
                team_id="aaaa1111-1111-1111-1111-111111111111",
                problem_id="aaaa0000-0000-0000-0000-000000000000",
                language="cpp",
                source_code="int main() {}",
            )
        )

    assert exc_info.value.http_status == 409
    assert exc_info.value.code is ApiErrorCode.CONTEST_NOT_OPEN


@pytest.mark.asyncio
async def test_submit_503_raises_temporarily_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503, json=_error_body("TEMPORARILY_UNAVAILABLE", "Try again later")
        )

    api = _api(httpx.MockTransport(handler))

    with pytest.raises(ApiError) as exc_info:
        await api.create_submission(
            client_module.SubmissionCreate(
                contest_id="11111111-1111-1111-1111-111111111111",
                team_id="aaaa1111-1111-1111-1111-111111111111",
                problem_id="aaaa0000-0000-0000-0000-000000000000",
                language="cpp",
                source_code="int main() {}",
            )
        )

    assert exc_info.value.http_status == 503
    assert exc_info.value.code is ApiErrorCode.TEMPORARILY_UNAVAILABLE


@pytest.mark.asyncio
async def test_network_error_maps_to_503():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    api = _api(httpx.MockTransport(handler))

    with pytest.raises(ApiError) as exc_info:
        await api.get_standings("11111111-1111-1111-1111-111111111111")

    assert exc_info.value.http_status == 503
    assert exc_info.value.code is ApiErrorCode.TEMPORARILY_UNAVAILABLE


@pytest.mark.asyncio
async def test_get_submission_processing_has_null_verdict():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(
            200,
            json={
                "submissionId": "3f1c2b9e-5a7d-4c1e-9b0a-1d2e3f4a5b6c",
                "status": "PROCESSING",
                "verdict": None,
                "createdAt": "2026-10-12T10:00:00Z",
                "updatedAt": "2026-10-12T10:00:02Z",
                "sequence": 2,
            },
        )

    api = _api(httpx.MockTransport(handler))

    submission = await api.get_submission("3f1c2b9e-5a7d-4c1e-9b0a-1d2e3f4a5b6c")

    assert submission.status is SubmissionStatus.PROCESSING
    assert submission.verdict is None
    assert submission.sequence == 2


@pytest.mark.asyncio
async def test_get_submission_not_found_raises_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404, json=_error_body("SUBMISSION_NOT_FOUND", "Submission not found")
        )

    api = _api(httpx.MockTransport(handler))

    with pytest.raises(ApiError) as exc_info:
        await api.get_submission("3f1c2b9e-5a7d-4c1e-9b0a-1d2e3f4a5b6c")

    assert exc_info.value.http_status == 404
    assert exc_info.value.code is ApiErrorCode.SUBMISSION_NOT_FOUND


@pytest.mark.asyncio
async def test_get_standings_parses_frozen_flag_and_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "contestId": "11111111-1111-1111-1111-111111111111",
                "frozen": True,
                "rows": [
                    {
                        "rank": 1,
                        "teamId": "aaaa1111-1111-1111-1111-111111111111",
                        "teamName": "Team Alpha",
                        "solved": 2,
                        "penalty": 100,
                        "problems": [],
                    }
                ],
            },
        )

    api = _api(httpx.MockTransport(handler))

    standings = await api.get_standings("11111111-1111-1111-1111-111111111111")

    assert standings.frozen is True
    assert standings.rows[0].team_name == "Team Alpha"
    assert standings.rows[0].solved == 2


@pytest.mark.asyncio
async def test_list_contests_teams_and_problems_parse_contract():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/contests":
            return httpx.Response(
                200,
                json=[
                    {
                        "contestId": "11111111-1111-1111-1111-111111111111",
                        "name": "Practice Round",
                        "startsAt": "2026-10-01T09:00:00Z",
                        "endsAt": "2026-12-01T18:00:00Z",
                        "isFrozen": False,
                    }
                ],
            )
        if path.endswith("/teams"):
            return httpx.Response(
                200,
                json=[
                    {
                        "teamId": "aaaa1111-1111-1111-1111-111111111111",
                        "contestId": "11111111-1111-1111-1111-111111111111",
                        "name": "Team Alpha",
                    }
                ],
            )
        return httpx.Response(
            200,
            json=[
                {
                    "problemId": "aaaa0000-0000-0000-0000-000000000000",
                    "contestId": "11111111-1111-1111-1111-111111111111",
                    "code": "A",
                    "title": "Hello, Verdict!",
                    "timeLimitMs": 2000,
                    "memoryLimitKb": 262144,
                }
            ],
        )

    api = _api(httpx.MockTransport(handler))

    contests = await api.list_contests()
    teams = await api.list_teams("11111111-1111-1111-1111-111111111111")
    problems = await api.list_problems("11111111-1111-1111-1111-111111111111")

    assert contests[0].name == "Practice Round"
    assert contests[0].is_frozen is False
    assert teams[0].name == "Team Alpha"
    assert problems[0].code == "A"
    assert problems[0].time_limit_ms == 2000
