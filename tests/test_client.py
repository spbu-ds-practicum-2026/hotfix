import pytest
from app.api.client import ApiClient
from app.models.contracts import SubmissionCreate, SubmissionStatus, Verdict


@pytest.mark.asyncio
async def test_mock_submission():
    client = ApiClient(use_mocks=True)

    result = await client.create_submission(
        SubmissionCreate(
            contest_id="contest-1",
            team_id="team-1",
            problem_id="A",
            language="cpp",
            source_code="int main() {}",
        )
    )

    assert result.status == SubmissionStatus.COMPLETED
    assert result.verdict == Verdict.ACCEPTED
    assert result.submission_id


@pytest.mark.asyncio
async def test_mock_standings():
    client = ApiClient(use_mocks=True)

    result = await client.get_standings("contest-1")

    assert result.contest_id == "contest-1"
    assert len(result.rows) >= 1
    assert result.rows[0].rank == 1
