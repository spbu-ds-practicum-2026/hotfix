
import pytest

pytest.importorskip("fastapi")
main = pytest.importorskip("app.main")

from app.api.errors import ApiError
from app.models.contracts import (
    ApiErrorCode,
    Submission,
    SubmissionStatus,
)
from fastapi.testclient import TestClient


@pytest.fixture
def ui():
    return TestClient(main.app)


@pytest.fixture
def restore_client():
    original = main.client
    yield
    main.client = original


class _FailingClient:
    def __init__(self, error: ApiError) -> None:
        self.error = error

    async def list_contests(self):
        raise self.error

    async def get_contest(self, contest_id):
        raise self.error

    async def list_teams(self, contest_id):
        raise self.error

    async def list_problems(self, contest_id):
        raise self.error

    async def get_standings(self, contest_id):
        raise self.error

    async def get_submission(self, submission_id):
        raise self.error

    async def create_submission(self, payload, idempotency_key=None):
        raise self.error


def _inject_queued_submission() -> str:
    submission = Submission(
        submission_id="99999999-9999-9999-9999-999999999999",
        contest_id="11111111-1111-1111-1111-111111111111",
        team_id="aaaa1111-1111-1111-1111-111111111111",
        problem_id="aaaa0000-0000-0000-0000-000000000000",
        language="cpp",
        status=SubmissionStatus.QUEUED,
        verdict=None,
        created_at="2026-10-15T12:00:00Z",
    )
    main.client.mock_submissions[submission.submission_id] = submission
    return submission.submission_id


def _inject_system_error_submission() -> str:
    submission = Submission(
        submission_id="88888888-8888-8888-8888-888888888888",
        contest_id="11111111-1111-1111-1111-111111111111",
        team_id="aaaa1111-1111-1111-1111-111111111111",
        problem_id="aaaa0000-0000-0000-0000-000000000000",
        language="cpp",
        status=SubmissionStatus.SYSTEM_ERROR,
        verdict=None,
        created_at="2026-10-15T12:00:00Z",
    )
    main.client.mock_submissions[submission.submission_id] = submission
    return submission.submission_id


def test_contests_list_shows_all_demo_contests(ui):
    response = ui.get("/")

    assert response.status_code == 200
    assert "Practice Round" in response.text
    assert "Frozen Demo" in response.text


def test_contest_page_shows_problems_teams_and_form(ui):
    contest_id = "11111111-1111-1111-1111-111111111111"

    response = ui.get(f"/contests/{contest_id}")

    assert response.status_code == 200
    assert "Hello, Verdict!" in response.text
    assert "Team Alpha" in response.text
    assert 'action="/submit"' in response.text

    assert '<option value="cpp">C++</option>' in response.text
    assert 'value="python"' not in response.text


def test_submission_loading_state_auto_refreshes(ui):
    submission_id = _inject_queued_submission()

    response = ui.get(f"/submissions/{submission_id}")

    assert response.status_code == 200
    assert "PROCESSING" in response.text
   
    assert 'http-equiv="refresh"' in response.text
    assert "обновится автоматически" in response.text


def test_submission_final_state_stops_refresh_and_shows_verdict(ui):
    submission_id = _inject_queued_submission()
    
    ui.get(f"/submissions/{submission_id}")
    response = ui.get(f"/submissions/{submission_id}")

    assert response.status_code == 200
    assert "COMPLETED" in response.text
    assert "ACCEPTED" in response.text
    assert 'http-equiv="refresh"' not in response.text


def test_submission_system_error_shows_banner_without_verdict(ui):
    submission_id = _inject_system_error_submission()

    response = ui.get(f"/submissions/{submission_id}")

    assert response.status_code == 200
    assert "SYSTEM_ERROR" in response.text
    assert "Системная ошибка" in response.text
    assert 'http-equiv="refresh"' not in response.text


def test_standings_frozen_banner(ui):
    contest_id = "22222222-2222-2222-2222-222222222222"

    response = ui.get(f"/contests/{contest_id}/standings")

    assert response.status_code == 200
    assert "заморожена" in response.text
    assert "Team Gamma" in response.text


def test_standings_empty_state(ui):
    contest_id = "33333333-3333-3333-3333-333333333333"

    response = ui.get(f"/contests/{contest_id}/standings")

    assert response.status_code == 200
    assert "пока пуста" in response.text
    assert "<tbody>" not in response.text


def test_error_404_banner(ui):
    response = ui.get("/contests/00000000-0000-0000-0000-00000000000f")

    assert response.status_code == 404
    assert "CONTEST_NOT_FOUND" in response.text


def test_error_503_banner(ui, restore_client):
    main.client = _FailingClient(
        ApiError(503, ApiErrorCode.TEMPORARILY_UNAVAILABLE, "Try again later")
    )

    response = ui.get("/")

    assert response.status_code == 503
    assert "TEMPORARILY_UNAVAILABLE" in response.text
    assert "временно недоступен" in response.text


def test_error_409_banner_on_submit(ui, restore_client):
    main.client = _FailingClient(
        ApiError(409, ApiErrorCode.CONTEST_NOT_OPEN, "Contest is not open")
    )

    response = ui.post(
        "/submit",
        data={
            "contest_id": "11111111-1111-1111-1111-111111111111",
            "team_id": "aaaa1111-1111-1111-1111-111111111111",
            "problem_id": "aaaa0000-0000-0000-0000-000000000000",
            "language": "cpp",
            "source_code": "int main() {}",
        },
    )

    assert response.status_code == 409
    assert "CONTEST_NOT_OPEN" in response.text


def test_empty_source_code_is_rejected(ui):
    response = ui.post(
        "/submit",
        data={
            "contest_id": "11111111-1111-1111-1111-111111111111",
            "team_id": "aaaa1111-1111-1111-1111-111111111111",
            "problem_id": "aaaa0000-0000-0000-0000-000000000000",
            "language": "cpp",
            "source_code": "   ",
        },
    )

    assert response.status_code == 400
    assert "VALIDATION_ERROR" in response.text


def test_unsupported_language_is_rejected(ui):
    response = ui.post(
        "/submit",
        data={
            "contest_id": "11111111-1111-1111-1111-111111111111",
            "team_id": "aaaa1111-1111-1111-1111-111111111111",
            "problem_id": "aaaa0000-0000-0000-0000-000000000000",
            "language": "python",
            "source_code": "print()",
        },
    )

    assert response.status_code == 400
    assert "VALIDATION_ERROR" in response.text
