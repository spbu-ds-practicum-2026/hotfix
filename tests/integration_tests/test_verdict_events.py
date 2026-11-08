
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

standings_service_module = pytest.importorskip("domain.standings_service")
models = pytest.importorskip("domain.models")
events_contract = pytest.importorskip("contracts.events")
statuses = pytest.importorskip("contracts.statuses")

CONTEST_ID = UUID("11111111-1111-1111-1111-111111111111")
TEAM_ID = UUID("aaaa1111-1111-1111-1111-111111111111")
PROBLEM_ID = UUID("aaaa0000-0000-0000-0000-000000000000")
PROBLEM_B_ID = UUID("bbbb0000-0000-0000-0000-000000000000")
SUBMITTED_AT = datetime(2026, 10, 15, 9, 10, 0, tzinfo=UTC)


def _make_service() -> standings_service_module.StandingsService:
    contest = models.Contest(
        contest_id=CONTEST_ID,
        name="Practice Round",
        starts_at=datetime(2026, 10, 15, 9, 0, 0, tzinfo=UTC),
        ends_at=datetime(2026, 10, 15, 14, 0, 0, tzinfo=UTC),
        is_frozen=False,
    )
    team = models.Team(team_id=TEAM_ID, contest_id=CONTEST_ID, name="Team Alpha")
    problem_a = models.Problem(
        problem_id=PROBLEM_ID,
        contest_id=CONTEST_ID,
        code="A",
        title="Hello, Verdict!",
    )
    problem_b = models.Problem(
        problem_id=PROBLEM_B_ID,
        contest_id=CONTEST_ID,
        code="B",
        title="Shard Routing",
    )
    return standings_service_module.StandingsService(
        contest=contest, teams=[team], problems=[problem_a, problem_b]
    )


def _verdict_event_body(
    event_id: UUID,
    submission_id: UUID,
    sequence: int,
    verdict: str = "ACCEPTED",
) -> str:
    event = events_contract.SubmissionVerdictEvent(
        event_id=event_id,
        trace_id=uuid4(),
        occurred_at=datetime(2026, 10, 15, 10, 10, 5, tzinfo=UTC),
        submission_id=submission_id,
        sequence=sequence,
        attempt_id=uuid4(),
        fencing_token=1,
        verdict=verdict,
    )
    return event.to_message_body()


def test_duplicate_event_id_is_not_counted_twice():
    service = _make_service()
    submission_id = uuid4()
    event_id = uuid4()

    first = service.apply_verdict_event(
        event_id, submission_id, TEAM_ID, PROBLEM_ID, "ACCEPTED", SUBMITTED_AT
    )
    duplicate = service.apply_verdict_event(
        event_id, submission_id, TEAM_ID, PROBLEM_ID, "ACCEPTED", SUBMITTED_AT
    )

    assert first is True
    assert duplicate is False
    assert len(service.attempts) == 1
    row = service.get_standings().rows[0]
    assert row.solved == 1
    assert row.penalty == 10  


def test_duplicate_submission_with_new_event_id_is_not_counted_twice():
    service = _make_service()
    submission_id = uuid4()

    assert (
        service.apply_verdict_event(
            uuid4(), submission_id, TEAM_ID, PROBLEM_ID, "ACCEPTED", SUBMITTED_AT
        )
        is True
    )
   
    assert (
        service.apply_verdict_event(
            uuid4(), submission_id, TEAM_ID, PROBLEM_ID, "ACCEPTED", SUBMITTED_AT
        )
        is False
    )
    assert len(service.attempts) == 1


def test_stale_sequence_does_not_decrease_last_sequence():
    service = _make_service()

    service.apply_verdict_event(
        uuid4(), uuid4(), TEAM_ID, PROBLEM_ID, "ACCEPTED", SUBMITTED_AT, sequence=2
    )
  
    service.apply_verdict_event(
        uuid4(), uuid4(), TEAM_ID, PROBLEM_ID, "WRONG_ANSWER", SUBMITTED_AT, sequence=1
    )

    assert service.last_sequence == 2
  
    assert len(service.attempts) == 2


def test_standings_recompute_is_idempotent():
    service = _make_service()
    service.apply_verdict_event(
        uuid4(), uuid4(), TEAM_ID, PROBLEM_ID, "WRONG_ANSWER", SUBMITTED_AT
    )
    service.apply_verdict_event(
        uuid4(), uuid4(), TEAM_ID, PROBLEM_ID, "ACCEPTED", SUBMITTED_AT
    )

    first = service.get_standings()
    second = service.get_standings()

    assert first.rows[0].solved == second.rows[0].solved == 1
 
    assert first.rows[0].penalty == second.rows[0].penalty == 30


def test_verdict_event_body_round_trips_into_standings():
   
    service = _make_service()
    submission_id = uuid4()
    event_id = uuid4()
    body = _verdict_event_body(event_id, submission_id, sequence=3)

    import json

    parsed = json.loads(body)
    assert parsed["schemaVersion"] == 1
    assert parsed["status"] == "COMPLETED"

    applied = service.apply_verdict_event(
        UUID(parsed["eventId"]),
        UUID(parsed["submissionId"]),
        TEAM_ID,
        PROBLEM_ID,
        parsed["verdict"],
        SUBMITTED_AT,
        sequence=parsed["sequence"],
    )

    assert applied is True
    assert service.last_sequence == 3
    assert service.get_standings().rows[0].solved == 1


def test_freeze_keeps_public_table_while_processing_continues():
    service = _make_service()
    service.apply_verdict_event(
        uuid4(), uuid4(), TEAM_ID, PROBLEM_ID, "ACCEPTED", SUBMITTED_AT
    )
    frozen = service.freeze()


    service.apply_verdict_event(
        uuid4(),
        uuid4(),
        TEAM_ID,
        PROBLEM_B_ID,
        "ACCEPTED",
        SUBMITTED_AT,
    )
    still_frozen = service.get_standings()

    assert frozen.rows[0].solved == 1
    assert still_frozen.rows[0].solved == 1

    unfrozen = service.unfreeze()
    assert unfrozen.rows[0].solved == 2
