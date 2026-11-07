from __future__ import annotations

from datetime import datetime
from uuid import UUID

from domain.models import (
    Contest,
    Problem,
    Standings,
    SubmissionAttempt,
    SubmissionStatus,
    Team,
    Verdict,
)
from domain.scoring import compute_standings


class StandingsService:
    def __init__(
        self,
        contest: Contest,
        teams: list[Team],
        problems: list[Problem],
        initial_attempts: list[SubmissionAttempt] | None = None,
        is_frozen: bool = False,
        include_team_names: bool = True,
        filter_problems_with_no_attempts: bool = False,
    ) -> None:
        self.contest = Contest(
            contest_id=contest.contest_id,
            name=contest.name,
            starts_at=contest.starts_at,
            ends_at=contest.ends_at,
            is_frozen=is_frozen or contest.is_frozen,
        )
        self.teams: dict[UUID, Team] = {t.team_id: t for t in teams}
        self.problems: dict[UUID, Problem] = {p.problem_id: p for p in problems}
        self.attempts: list[SubmissionAttempt] = list(initial_attempts or [])
        self.processed_event_ids: set[UUID] = set()
        self.processed_submission_ids: set[UUID] = {a.submission_id for a in self.attempts}
        self.include_team_names = include_team_names
        self.filter_problems_with_no_attempts = filter_problems_with_no_attempts
        self.last_sequence: int = 0

        self.frozen_snapshot: Standings | None = None
        if self.contest.is_frozen:
            self._take_frozen_snapshot()

    def _group_attempts_by_team(self) -> dict[UUID, list[SubmissionAttempt]]:
        by_team: dict[UUID, list[SubmissionAttempt]] = {}
        for att in self.attempts:
            by_team.setdefault(att.team_id, []).append(att)
        return by_team

    def _compute_actual_standings(self, frozen_flag: bool = False) -> Standings:
        attempts_by_team = self._group_attempts_by_team()
        return compute_standings(
            contest=self.contest,
            teams=list(self.teams.values()),
            problems=list(self.problems.values()),
            attempts_by_team=attempts_by_team,
            frozen=frozen_flag,
            last_event_sequence=self.last_sequence,
            include_team_names=self.include_team_names,
            filter_problems_with_no_attempts=self.filter_problems_with_no_attempts,
        )

    def _take_frozen_snapshot(self) -> None:
        self.frozen_snapshot = self._compute_actual_standings(frozen_flag=True)

    def freeze(self) -> Standings:
        self.contest = Contest(
            contest_id=self.contest.contest_id,
            name=self.contest.name,
            starts_at=self.contest.starts_at,
            ends_at=self.contest.ends_at,
            is_frozen=True,
        )
        self._take_frozen_snapshot()
        return self.frozen_snapshot

    def unfreeze(self) -> Standings:
        self.contest = Contest(
            contest_id=self.contest.contest_id,
            name=self.contest.name,
            starts_at=self.contest.starts_at,
            ends_at=self.contest.ends_at,
            is_frozen=False,
        )
        self.frozen_snapshot = None
        return self._compute_actual_standings(frozen_flag=False)

    def get_standings(self) -> Standings:
        if self.contest.is_frozen and self.frozen_snapshot is not None:
            return self.frozen_snapshot
        return self._compute_actual_standings(frozen_flag=self.contest.is_frozen)

    def apply_verdict_event(
        self,
        event_id: UUID,
        submission_id: UUID,
        team_id: UUID,
        problem_id: UUID,
        verdict: Verdict | str,
        submitted_at: datetime,
        sequence: int = 1,
    ) -> bool:
        if event_id in self.processed_event_ids or submission_id in self.processed_submission_ids:
            return False

        if isinstance(verdict, str):
            verdict = Verdict(verdict)

        attempt = SubmissionAttempt(
            submission_id=submission_id,
            contest_id=self.contest.contest_id,
            team_id=team_id,
            problem_id=problem_id,
            status=SubmissionStatus.COMPLETED,
            verdict=verdict,
            created_at=submitted_at,
        )
        self.attempts.append(attempt)
        self.processed_event_ids.add(event_id)
        self.processed_submission_ids.add(submission_id)

        if sequence > self.last_sequence:
            self.last_sequence = sequence

        return True
