from __future__ import annotations

import unittest
from datetime import datetime
from uuid import UUID

from api.contests import (
    freeze_contest_standings,
    get_contest_details,
    get_contest_standings,
    list_contest_problems,
    list_contest_teams,
    list_contests,
    set_domain_repository,
    unfreeze_contest_standings,
)
from contracts.errors import ApiError, ErrorCode
from domain.models import (
    Contest,
    Problem,
    SubmissionAttempt,
    SubmissionStatus,
    Team,
    Verdict,
)
from domain.repository import InMemoryDomainRepository, ShardedDomainRepository


def make_utc(dt_str: str) -> datetime:
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))


class TestContestsApi(unittest.TestCase):
    def setUp(self) -> None:
        self.contest_id = UUID("11111111-1111-1111-1111-111111111111")
        self.contest = Contest(
            contest_id=self.contest_id,
            name="SPbU Practice 2026",
            starts_at=make_utc("2026-10-15T10:00:00Z"),
            ends_at=make_utc("2026-10-15T15:00:00Z"),
            is_frozen=False,
        )
        self.team1 = Team(
            team_id=UUID("aaaa1111-1111-1111-1111-111111111111"),
            contest_id=self.contest_id,
            name="Team Alpha",
        )
        self.team2 = Team(
            team_id=UUID("bbbb2222-2222-2222-2222-222222222222"),
            contest_id=self.contest_id,
            name="Team Beta",
        )
        self.prob1 = Problem(
            problem_id=UUID("aaaa0000-0000-0000-0000-000000000000"),
            contest_id=self.contest_id,
            code="A",
            title="Problem A",
            time_limit_ms=1000,
            memory_limit_kb=65536,
        )
        self.prob2 = Problem(
            problem_id=UUID("bbbb0000-0000-0000-0000-000000000000"),
            contest_id=self.contest_id,
            code="B",
            title="Problem B",
            time_limit_ms=2000,
            memory_limit_kb=262144,
        )

        self.repo = InMemoryDomainRepository(
            contests=[self.contest],
            teams=[self.team1, self.team2],
            problems=[self.prob1, self.prob2],
        )
        set_domain_repository(self.repo)

    def test_list_contests(self) -> None:
        res = list_contests(self.repo)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["contestId"], str(self.contest_id))
        self.assertEqual(res[0]["name"], "SPbU Practice 2026")

    def test_get_contest_details_success(self) -> None:
        res = get_contest_details(self.contest_id, self.repo)
        self.assertEqual(res["contestId"], str(self.contest_id))
        self.assertFalse(res["isFrozen"])

    def test_get_contest_details_not_found(self) -> None:
        unknown_id = UUID("00000000-0000-0000-0000-999999999999")
        with self.assertRaises(ApiError) as ctx:
            get_contest_details(unknown_id, self.repo)
        self.assertEqual(ctx.exception.http_status, 404)
        self.assertEqual(ctx.exception.code, ErrorCode.CONTEST_NOT_FOUND)

    def test_list_contest_teams(self) -> None:
        teams = list_contest_teams(self.contest_id, self.repo)
        self.assertEqual(len(teams), 2)
        names = {t["name"] for t in teams}
        self.assertEqual(names, {"Team Alpha", "Team Beta"})

    def test_list_contest_problems(self) -> None:
        problems = list_contest_problems(self.contest_id, self.repo)
        self.assertEqual(len(problems), 2)
        codes = [p["code"] for p in problems]
        self.assertEqual(codes, ["A", "B"])

    def test_standings_lifecycle_freeze_and_unfreeze(self) -> None:
        st = get_contest_standings(self.contest_id, self.repo)
        self.assertFalse(st["frozen"])
        self.assertEqual(len(st["rows"]), 2)

        frozen_st = freeze_contest_standings(self.contest_id, self.repo)
        self.assertTrue(frozen_st["frozen"])

        contest_details = get_contest_details(self.contest_id, self.repo)
        self.assertTrue(contest_details["isFrozen"])

        current_st = get_contest_standings(self.contest_id, self.repo)
        self.assertTrue(current_st["frozen"])

        unfrozen_st = unfreeze_contest_standings(self.contest_id, self.repo)
        self.assertFalse(unfrozen_st["frozen"])

        contest_details_unfrozen = get_contest_details(self.contest_id, self.repo)
        self.assertFalse(contest_details_unfrozen["isFrozen"])

    def test_standings_connected_to_contest_submissions(self) -> None:
        sub1 = SubmissionAttempt(
            submission_id=UUID("00000000-0000-0000-0000-000000000101"),
            contest_id=self.contest_id,
            team_id=self.team1.team_id,
            problem_id=self.prob1.problem_id,
            status=SubmissionStatus.COMPLETED,
            verdict=Verdict.WRONG_ANSWER,
            created_at=make_utc("2026-10-15T10:10:00Z"),
        )
        sub2 = SubmissionAttempt(
            submission_id=UUID("00000000-0000-0000-0000-000000000102"),
            contest_id=self.contest_id,
            team_id=self.team1.team_id,
            problem_id=self.prob1.problem_id,
            status=SubmissionStatus.COMPLETED,
            verdict=Verdict.ACCEPTED,
            created_at=make_utc("2026-10-15T10:25:00Z"),
        )
        self.repo.add_submission(sub1)
        self.repo.add_submission(sub2)
        set_domain_repository(self.repo)

        st = get_contest_standings(self.contest_id, self.repo)
        self.assertEqual(st["rows"][0]["teamId"], str(self.team1.team_id))
        self.assertEqual(st["rows"][0]["solved"], 1)
        self.assertEqual(st["rows"][0]["penalty"], 45)

    def test_standings_after_contest_rebalanced_between_shards(self) -> None:
        shard_0 = InMemoryDomainRepository(
            contests=[self.contest],
            teams=[self.team1, self.team2],
            problems=[self.prob1, self.prob2],
            submissions=[
                SubmissionAttempt(
                    submission_id=UUID("00000000-0000-0000-0000-000000000201"),
                    contest_id=self.contest_id,
                    team_id=self.team2.team_id,
                    problem_id=self.prob2.problem_id,
                    status=SubmissionStatus.COMPLETED,
                    verdict=Verdict.ACCEPTED,
                    created_at=make_utc("2026-10-15T10:15:00Z"),
                )
            ],
        )
        shard_1 = InMemoryDomainRepository()
        sharded_repo = ShardedDomainRepository(
            shards={0: shard_0, 1: shard_1},
            shard_selector=lambda _cid: 0,
        )
        set_domain_repository(sharded_repo)

        before_migration = get_contest_standings(self.contest_id, sharded_repo)
        self.assertEqual(before_migration["rows"][0]["teamId"], str(self.team2.team_id))
        self.assertEqual(before_migration["rows"][0]["solved"], 1)
        self.assertEqual(before_migration["rows"][0]["penalty"], 15)

        contest_on_shard_0 = shard_0.get_contest(self.contest_id)
        if contest_on_shard_0 is not None:
            shard_1.add_contest(contest_on_shard_0)
        for team in shard_0.list_teams(self.contest_id):
            shard_1.add_team(team)
        for prob in shard_0.list_problems(self.contest_id):
            shard_1.add_problem(prob)
        for sub in shard_0.list_submissions(self.contest_id):
            shard_1.add_submission(sub)
        saved_st = shard_0.get_standings(self.contest_id)
        if saved_st is not None:
            shard_1.save_standings(saved_st)

        shard_0._contests.pop(self.contest_id, None)
        shard_0._teams.pop(self.contest_id, None)
        shard_0._problems.pop(self.contest_id, None)
        shard_0._submissions.pop(self.contest_id, None)
        shard_0._standings.pop(self.contest_id, None)
        sharded_repo.set_shard_selector(lambda _cid: 1)

        after_migration = get_contest_standings(self.contest_id, sharded_repo)
        self.assertEqual(after_migration, before_migration)
        self.assertEqual(len(list_contest_teams(self.contest_id, sharded_repo)), 2)
        self.assertEqual(len(list_contest_problems(self.contest_id, sharded_repo)), 2)
