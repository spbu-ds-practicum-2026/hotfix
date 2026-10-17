from app.models.contracts import Submission, SubmissionCreate, SubmissionStatus


def test_submission_create_contract():
    payload = SubmissionCreate(
        contest_id="contest-1",
        team_id="team-1",
        problem_id="A",
        language="cpp",
        source_code="int main() {}",
    )

    assert payload.language == "cpp"
    assert payload.problem_id == "A"


def test_submission_status_values_are_fixed():
    assert SubmissionStatus.QUEUED.value == "QUEUED"
    assert SubmissionStatus.PROCESSING.value == "PROCESSING"
    assert SubmissionStatus.COMPLETED.value == "COMPLETED"
    assert SubmissionStatus.SYSTEM_ERROR.value == "SYSTEM_ERROR"


def test_submission_response_contract():
    submission = Submission(
        submission_id="sub-1",
        contest_id="contest-1",
        team_id="team-1",
        problem_id="A",
        language="cpp",
        status=SubmissionStatus.COMPLETED,
        verdict="ACCEPTED",
        created_at="2026-10-15T12:00:00Z",
    )

    assert submission.submission_id == "sub-1"
    assert submission.status == SubmissionStatus.COMPLETED
