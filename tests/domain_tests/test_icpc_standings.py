from __future__ import annotations

import json
import unittest
from datetime import datetime
from pathlib import Path
from uuid import UUID

from domain.models import (
    Contest,
    Problem,
    ProblemResult,
    SubmissionAttempt,
    SubmissionStatus,
    Team,
    Verdict,
)
from domain.scoring import (
    compute_problem_result,
    compute_standings,
)
from domain.standings_service import StandingsService

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "standings"


def parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


class TestIcpcScoring(unittest.TestCase):
    def test_first_accepted_calculates_penalty_and_ignores_subsequent_attempts(self) -> None:
        starts_at = parse_iso("2026-10-15T10:00:00Z")
        prob_id = UUID("aaaa0000-0000-0000-0000-000000000000")
        team_id = UUID("aaaa1111-1111-1111-1111-111111111111")
        contest_id = UUID("11111111-1111-1111-1111-111111111111")

        attempts = [
            SubmissionAttempt(
                submission_id=UUID("00000000-0000-0000-0000-000000000001"),
                contest_id=contest_id,
                team_id=team_id,
                problem_id=prob_id,
                status=SubmissionStatus.COMPLETED,
                verdict=Verdict.WRONG_ANSWER,
                created_at=parse_iso("2026-10-15T10:10:00Z"),
            ),
            SubmissionAttempt(
                submission_id=UUID("00000000-0000-0000-0000-000000000002"),
                contest_id=contest_id,
                team_id=team_id,
                problem_id=prob_id,
                status=SubmissionStatus.COMPLETED,
                verdict=Verdict.TIME_LIMIT_EXCEEDED,
                created_at=parse_iso("2026-10-15T10:20:00Z"),
            ),
            SubmissionAttempt(
                submission_id=UUID("00000000-0000-0000-0000-000000000003"),
                contest_id=contest_id,
                team_id=team_id,
                problem_id=prob_id,
                status=SubmissionStatus.COMPLETED,
                verdict=Verdict.COMPILATION_ERROR,
                created_at=parse_iso("2026-10-15T10:30:00Z"),
            ),
            SubmissionAttempt(
                submission_id=UUID("00000000-0000-0000-0000-000000000004"),
                contest_id=contest_id,
                team_id=team_id,
                problem_id=prob_id,
                status=SubmissionStatus.COMPLETED,
                verdict=Verdict.SYSTEM_ERROR,
                created_at=parse_iso("2026-10-15T10:40:00Z"),
            ),
            SubmissionAttempt(
                submission_id=UUID("00000000-0000-0000-0000-000000000005"),
                contest_id=contest_id,
                team_id=team_id,
                problem_id=prob_id,
                status=SubmissionStatus.COMPLETED,
                verdict=Verdict.ACCEPTED,
                created_at=parse_iso("2026-10-15T10:50:00Z"),
            ),
            SubmissionAttempt(
                submission_id=UUID("00000000-0000-0000-0000-000000000006"),
                contest_id=contest_id,
                team_id=team_id,
                problem_id=prob_id,
                status=SubmissionStatus.COMPLETED,
                verdict=Verdict.WRONG_ANSWER,
                created_at=parse_iso("2026-10-15T10:55:00Z"),
            ),
        ]

        res = compute_problem_result(prob_id, attempts, starts_at)

        self.assertTrue(res.accepted)
        self.assertEqual(res.accepted_at, parse_iso("2026-10-15T10:50:00Z"))
        self.assertEqual(res.attempts, 3)
        self.assertEqual(res.penalty, 90)

    def test_unaccepted_problem_has_zero_penalty(self) -> None:
        starts_at = parse_iso("2026-10-15T10:00:00Z")
        prob_id = UUID("aaaa0000-0000-0000-0000-000000000000")
        team_id = UUID("cccc3333-3333-3333-3333-333333333333")
        contest_id = UUID("11111111-1111-1111-1111-111111111111")

        attempts = [
            SubmissionAttempt(
                submission_id=UUID(f"00000000-0000-0000-0000-00000000000{i}"),
                contest_id=contest_id,
                team_id=team_id,
                problem_id=prob_id,
                status=SubmissionStatus.COMPLETED,
                verdict=Verdict.WRONG_ANSWER,
                created_at=parse_iso(f"2026-10-15T10:0{i}:00Z"),
            )
            for i in range(1, 6)
        ]

        res = compute_problem_result(prob_id, attempts, starts_at)

        self.assertFalse(res.accepted)
        self.assertIsNone(res.accepted_at)
        self.assertEqual(res.attempts, 5)
        self.assertEqual(res.penalty, 0)


class TestFixturesValidation(unittest.TestCase):
    def test_standings_normal_matches_fixture(self) -> None:
        fixture_path = FIXTURES_DIR / "standings-normal.json"
        with open(fixture_path, "r", encoding="utf-8") as f:
            expected_json = json.load(f)

        contest = Contest(
            contest_id=UUID(expected_json["contestId"]),
            name="ICPC Practice",
            starts_at=parse_iso("2026-10-15T10:00:00Z"),
            ends_at=parse_iso("2026-10-15T15:00:00Z"),
            is_frozen=False,
        )

        prob_a = Problem(
            problem_id=UUID("aaaa0000-0000-0000-0000-000000000000"),
            contest_id=contest.contest_id,
            code="A",
            title="Problem A",
        )
        prob_b = Problem(
            problem_id=UUID("bbbb0000-0000-0000-0000-000000000000"),
            contest_id=contest.contest_id,
            code="B",
            title="Problem B",
        )
        problems = [prob_a, prob_b]

        team_a = Team(UUID("aaaa1111-1111-1111-1111-111111111111"), contest.contest_id, "Team A")
        team_b = Team(UUID("bbbb2222-2222-2222-2222-222222222222"), contest.contest_id, "Team B")
        team_c = Team(UUID("cccc3333-3333-3333-3333-333333333333"), contest.contest_id, "Team C")
        teams = [team_a, team_b, team_c]

        attempts_by_team: dict[UUID, list[SubmissionAttempt]] = {
            team_a.team_id: [
                SubmissionAttempt(
                    UUID("00000000-0000-0000-0000-0000000000a1"),
                    contest.contest_id,
                    team_a.team_id,
                    prob_a.problem_id,
                    SubmissionStatus.COMPLETED,
                    Verdict.ACCEPTED,
                    parse_iso("2026-10-15T10:10:00Z"),
                ),
                SubmissionAttempt(
                    UUID("00000000-0000-0000-0000-0000000000b1"),
                    contest.contest_id,
                    team_a.team_id,
                    prob_b.problem_id,
                    SubmissionStatus.COMPLETED,
                    Verdict.WRONG_ANSWER,
                    parse_iso("2026-10-15T10:20:00Z"),
                ),
                SubmissionAttempt(
                    UUID("00000000-0000-0000-0000-0000000000b2"),
                    contest.contest_id,
                    team_a.team_id,
                    prob_b.problem_id,
                    SubmissionStatus.COMPLETED,
                    Verdict.WRONG_ANSWER,
                    parse_iso("2026-10-15T10:30:00Z"),
                ),
                SubmissionAttempt(
                    UUID("00000000-0000-0000-0000-0000000000b3"),
                    contest.contest_id,
                    team_a.team_id,
                    prob_b.problem_id,
                    SubmissionStatus.COMPLETED,
                    Verdict.ACCEPTED,
                    parse_iso("2026-10-15T10:50:00Z"),
                ),
            ],
            team_b.team_id: [
                SubmissionAttempt(
                    UUID("00000000-0000-0000-0000-000000000ba1"),
                    contest.contest_id,
                    team_b.team_id,
                    prob_a.problem_id,
                    SubmissionStatus.COMPLETED,
                    Verdict.WRONG_ANSWER,
                    parse_iso("2026-10-15T10:05:00Z"),
                ),
                SubmissionAttempt(
                    UUID("00000000-0000-0000-0000-000000000ba2"),
                    contest.contest_id,
                    team_b.team_id,
                    prob_a.problem_id,
                    SubmissionStatus.COMPLETED,
                    Verdict.ACCEPTED,
                    parse_iso("2026-10-15T10:10:00Z"),
                ),
                SubmissionAttempt(
                    UUID("00000000-0000-0000-0000-000000000bb1"),
                    contest.contest_id,
                    team_b.team_id,
                    prob_b.problem_id,
                    SubmissionStatus.COMPLETED,
                    Verdict.WRONG_ANSWER,
                    parse_iso("2026-10-15T10:15:00Z"),
                ),
            ],
            team_c.team_id: [
                SubmissionAttempt(
                    UUID(f"00000000-0000-0000-0000-0000000000c{i}"),
                    contest.contest_id,
                    team_c.team_id,
                    prob_a.problem_id,
                    SubmissionStatus.COMPLETED,
                    Verdict.WRONG_ANSWER,
                    parse_iso(f"2026-10-15T10:0{i}:00Z"),
                )
                for i in range(1, 6)
            ],
        }

        standings = compute_standings(
            contest=contest,
            teams=teams,
            problems=problems,
            attempts_by_team=attempts_by_team,
            frozen=False,
            include_team_names=False,
            filter_problems_with_no_attempts=True,
        )

        self.assertEqual(standings.to_dict(), expected_json)

    def test_standings_tie_break_matches_fixture(self) -> None:
        fixture_path = FIXTURES_DIR / "standings-tie-break.json"
        with open(fixture_path, "r", encoding="utf-8") as f:
            expected_json = json.load(f)

        contest = Contest(
            contest_id=UUID(expected_json["contestId"]),
            name="ICPC Tie Break",
            starts_at=parse_iso("2026-10-15T10:00:00Z"),
            ends_at=parse_iso("2026-10-15T15:00:00Z"),
            is_frozen=False,
        )

        prob_a = Problem(
            problem_id=UUID("aaaa0000-0000-0000-0000-000000000000"),
            contest_id=contest.contest_id,
            code="A",
            title="Problem A",
        )
        prob_b = Problem(
            problem_id=UUID("bbbb0000-0000-0000-0000-000000000000"),
            contest_id=contest.contest_id,
            code="B",
            title="Problem B",
        )
        problems = [prob_a, prob_b]

        team_b = Team(UUID("bbbb2222-2222-2222-2222-222222222222"), contest.contest_id, "Team B")
        team_a = Team(UUID("aaaa1111-1111-1111-1111-111111111111"), contest.contest_id, "Team A")
        teams = [team_a, team_b]

        attempts_by_team: dict[UUID, list[SubmissionAttempt]] = {
            team_b.team_id: [
                SubmissionAttempt(
                    UUID("00000000-0000-0000-0000-0000000000b1"),
                    contest.contest_id,
                    team_b.team_id,
                    prob_a.problem_id,
                    SubmissionStatus.COMPLETED,
                    Verdict.ACCEPTED,
                    parse_iso("2026-10-15T10:20:00Z"),
                ),
                SubmissionAttempt(
                    UUID("00000000-0000-0000-0000-0000000000b2"),
                    contest.contest_id,
                    team_b.team_id,
                    prob_b.problem_id,
                    SubmissionStatus.COMPLETED,
                    Verdict.ACCEPTED,
                    parse_iso("2026-10-15T10:50:00Z"),
                ),
            ],
            team_a.team_id: [
                SubmissionAttempt(
                    UUID("00000000-0000-0000-0000-0000000000a1"),
                    contest.contest_id,
                    team_a.team_id,
                    prob_a.problem_id,
                    SubmissionStatus.COMPLETED,
                    Verdict.WRONG_ANSWER,
                    parse_iso("2026-10-15T10:05:00Z"),
                ),
                SubmissionAttempt(
                    UUID("00000000-0000-0000-0000-0000000000a2"),
                    contest.contest_id,
                    team_a.team_id,
                    prob_a.problem_id,
                    SubmissionStatus.COMPLETED,
                    Verdict.ACCEPTED,
                    parse_iso("2026-10-15T10:10:00Z"),
                ),
                SubmissionAttempt(
                    UUID("00000000-0000-0000-0000-0000000000a3"),
                    contest.contest_id,
                    team_a.team_id,
                    prob_b.problem_id,
                    SubmissionStatus.COMPLETED,
                    Verdict.ACCEPTED,
                    parse_iso("2026-10-15T11:00:00Z"),
                ),
            ],
        }

        standings = compute_standings(
            contest=contest,
            teams=teams,
            problems=problems,
            attempts_by_team=attempts_by_team,
            frozen=False,
            include_team_names=False,
        )

        self.assertEqual(standings.to_dict(), expected_json)
        self.assertEqual(standings.rows[0].team_id, team_b.team_id)
        self.assertEqual(standings.rows[0].rank, 1)
        self.assertEqual(standings.rows[1].team_id, team_a.team_id)
        self.assertEqual(standings.rows[1].rank, 2)

    def test_standings_frozen_matches_fixture_and_service_lifecycle(self) -> None:
        fixture_path = FIXTURES_DIR / "standings-frozen.json"
        with open(fixture_path, "r", encoding="utf-8") as f:
            frozen_fixture_json = json.load(f)

        contest = Contest(
            contest_id=UUID(frozen_fixture_json["contestId"]),
            name="ICPC Freeze Test",
            starts_at=parse_iso("2026-10-15T10:00:00Z"),
            ends_at=parse_iso("2026-10-15T15:00:00Z"),
            is_frozen=False,
        )
        prob_b = Problem(
            problem_id=UUID("bbbb0000-0000-0000-0000-000000000000"),
            contest_id=contest.contest_id,
            code="B",
            title="Problem B",
        )
        problems = [prob_b]

        team_c = Team(UUID("cccc3333-3333-3333-3333-333333333333"), contest.contest_id, "Team C")
        team_a = Team(UUID("aaaa1111-1111-1111-1111-111111111111"), contest.contest_id, "Team A")
        teams = [team_c, team_a]

        initial_attempts = [
            SubmissionAttempt(
                UUID("00000000-0000-0000-0000-0000000000c1"),
                contest.contest_id,
                team_c.team_id,
                prob_b.problem_id,
                SubmissionStatus.COMPLETED,
                Verdict.ACCEPTED,
                parse_iso("2026-10-15T10:50:00Z"),
            ),
            SubmissionAttempt(
                UUID("00000000-0000-0000-0000-0000000000a1"),
                contest.contest_id,
                team_a.team_id,
                prob_b.problem_id,
                SubmissionStatus.COMPLETED,
                Verdict.WRONG_ANSWER,
                parse_iso("2026-10-15T10:45:00Z"),
            ),
        ]

        service = StandingsService(
            contest=contest,
            teams=teams,
            problems=problems,
            initial_attempts=initial_attempts,
            is_frozen=False,
            include_team_names=False,
        )

        service.freeze()
        frozen_public = service.get_standings()
        self.assertEqual(frozen_public.to_dict(), frozen_fixture_json)

        new_event_id = UUID("eeee0000-0000-0000-0000-000000000001")
        new_sub_id = UUID("00000000-0000-0000-0000-0000000000a2")
        applied = service.apply_verdict_event(
            event_id=new_event_id,
            submission_id=new_sub_id,
            team_id=team_a.team_id,
            problem_id=prob_b.problem_id,
            verdict=Verdict.ACCEPTED,
            submitted_at=parse_iso("2026-10-15T11:20:00Z"),
        )
        self.assertTrue(applied)
        self.assertEqual(service.get_standings().to_dict(), frozen_fixture_json)

        duplicate_applied = service.apply_verdict_event(
            event_id=new_event_id,
            submission_id=new_sub_id,
            team_id=team_a.team_id,
            problem_id=prob_b.problem_id,
            verdict=Verdict.ACCEPTED,
            submitted_at=parse_iso("2026-10-15T11:20:00Z"),
        )
        self.assertFalse(duplicate_applied)
        self.assertEqual(len(service.attempts), 3)

        unfrozen_standings = service.unfreeze()
        self.assertFalse(unfrozen_standings.frozen)
        team_a_row = next(r for r in unfrozen_standings.rows if r.team_id == team_a.team_id)
        self.assertEqual(team_a_row.solved, 1)
        self.assertEqual(team_a_row.penalty, 100)
        self.assertEqual(team_a_row.rank, 2)
