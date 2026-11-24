"""Standings всегда считается по свежим данным БД, freeze отдаёт снимок (services/api/src/api/contests.py)."""

import unittest
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from api import contests
from domain.models import Contest, Problem, SubmissionAttempt, SubmissionStatus, Team, Verdict
from domain.repository import InMemoryDomainRepository

START = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)


class FreshStandingsTest(unittest.TestCase):
    def setUp(self) -> None:
        contests._active_standings_services.clear()
        self.contest = Contest(uuid4(), "Demo", START, START + timedelta(days=30))
        self.alpha = Team(uuid4(), self.contest.contest_id, "Alpha")
        self.beta = Team(uuid4(), self.contest.contest_id, "Beta")
        self.problem = Problem(uuid4(), self.contest.contest_id, "A", "A+B")
        self.repo = InMemoryDomainRepository(
            contests=[self.contest], teams=[self.alpha, self.beta], problems=[self.problem]
        )

    def _attempt(self, team, minutes, status, verdict, submission_id=None) -> SubmissionAttempt:
        attempt = SubmissionAttempt(
            submission_id or uuid4(),
            self.contest.contest_id,
            team.team_id,
            self.problem.problem_id,
            status,
            verdict,
            START + timedelta(minutes=minutes),
        )
        self.repo.add_submission(attempt)
        return attempt

    def _rows(self):
        table = contests.get_contest_standings(self.contest.contest_id, self.repo)
        return {row["teamName"] if "teamName" in row else row["team_name"]: row for row in table["rows"]}, table

    def test_verdict_that_arrives_after_first_view_is_counted(self) -> None:
        submission_id = uuid4()
        self._attempt(self.alpha, 12, SubmissionStatus.QUEUED, None, submission_id)
        first, _ = self._rows()
        self.assertEqual(first["Alpha"]["solved"], 0)

        self._attempt(self.alpha, 12, SubmissionStatus.COMPLETED, Verdict.ACCEPTED, submission_id)

        second, _ = self._rows()
        self.assertEqual((second["Alpha"]["solved"], second["Alpha"]["penalty"]), (1, 12))

    def test_saved_table_does_not_hide_new_verdicts_after_restart(self) -> None:
        self._attempt(self.alpha, 12, SubmissionStatus.COMPLETED, Verdict.ACCEPTED)
        self._rows()  # таблица сохранена
        contests._active_standings_services.clear()  # перезапуск API
        self._attempt(self.beta, 20, SubmissionStatus.COMPLETED, Verdict.ACCEPTED)

        rows, _ = self._rows()

        self.assertEqual(rows["Beta"]["solved"], 1)

    def test_freeze_returns_snapshot_and_unfreeze_applies_everything(self) -> None:
        self._attempt(self.alpha, 12, SubmissionStatus.COMPLETED, Verdict.ACCEPTED)
        contests.freeze_contest_standings(self.contest.contest_id, self.repo)

        self._attempt(self.beta, 20, SubmissionStatus.COMPLETED, Verdict.ACCEPTED)
        rows, table = self._rows()
        self.assertTrue(table["frozen"])
        self.assertEqual(rows["Beta"]["solved"], 0)  # снимок не изменился

        contests.unfreeze_contest_standings(self.contest.contest_id, self.repo)
        rows, table = self._rows()
        self.assertFalse(table["frozen"])
        self.assertEqual(rows["Beta"]["solved"], 1)

    def test_repeated_freeze_keeps_the_same_snapshot(self) -> None:
        self._attempt(self.alpha, 12, SubmissionStatus.COMPLETED, Verdict.ACCEPTED)
        contests.freeze_contest_standings(self.contest.contest_id, self.repo)
        self._attempt(self.beta, 20, SubmissionStatus.COMPLETED, Verdict.ACCEPTED)

        again = contests.freeze_contest_standings(self.contest.contest_id, self.repo)

        solved = {row.get("teamName", row.get("team_name")): row["solved"] for row in again["rows"]}
        self.assertEqual(solved["Beta"], 0)


if __name__ == "__main__":
    unittest.main()
