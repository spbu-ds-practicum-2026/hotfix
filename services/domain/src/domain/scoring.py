from __future__ import annotations

from datetime import datetime
from uuid import UUID

from domain.models import (
    Contest,
    Problem,
    ProblemResult,
    StandingRow,
    Standings,
    SubmissionAttempt,
    Team,
    Verdict,
)

PENALTY_MINUTES_PER_REJECTED_ATTEMPT = 20


def compute_problem_result(
    problem_id: UUID,
    attempts: list[SubmissionAttempt],
    contest_starts_at: datetime,
) -> ProblemResult:
    sorted_attempts = sorted(attempts, key=lambda a: a.created_at)

    failed_attempts = 0
    first_accepted_attempt: SubmissionAttempt | None = None

    for attempt in sorted_attempts:
        if attempt.verdict is None:
            continue

        if attempt.verdict.is_accepted:
            first_accepted_attempt = attempt
            break

        if attempt.verdict.is_penalized:
            failed_attempts += 1
        elif attempt.verdict.is_ignored_in_penalty:
            continue

    if first_accepted_attempt is not None:
        accepted_at = first_accepted_attempt.created_at
        delta_seconds = max(0.0, (accepted_at - contest_starts_at).total_seconds())
        minutes_from_start = int(delta_seconds // 60)
        total_penalty = minutes_from_start + failed_attempts * PENALTY_MINUTES_PER_REJECTED_ATTEMPT
        return ProblemResult(
            problem_id=problem_id,
            attempts=failed_attempts + 1,
            accepted=True,
            accepted_at=accepted_at,
            penalty=total_penalty,
        )

    return ProblemResult(
        problem_id=problem_id,
        attempts=failed_attempts,
        accepted=False,
        accepted_at=None,
        penalty=0,
    )


def compute_standings(
    contest: Contest,
    teams: list[Team],
    problems: list[Problem],
    attempts_by_team: dict[UUID, list[SubmissionAttempt]],
    frozen: bool = False,
    last_event_sequence: int = 0,
    include_team_names: bool = True,
    filter_problems_with_no_attempts: bool = False,
) -> Standings:
    rows_data: list[tuple[int, int, UUID, str, list[ProblemResult]]] = []

    for team in teams:
        team_attempts = attempts_by_team.get(team.team_id, [])
        attempts_by_problem: dict[UUID, list[SubmissionAttempt]] = {}
        for att in team_attempts:
            attempts_by_problem.setdefault(att.problem_id, []).append(att)

        problem_results: list[ProblemResult] = []
        for problem in problems:
            prob_attempts = attempts_by_problem.get(problem.problem_id, [])
            if filter_problems_with_no_attempts and not prob_attempts:
                continue
            res = compute_problem_result(
                problem.problem_id,
                prob_attempts,
                contest.starts_at,
            )
            if filter_problems_with_no_attempts and res.attempts == 0 and not res.accepted:
                continue
            problem_results.append(res)

        solved = sum(1 for p in problem_results if p.accepted)
        penalty = sum(p.penalty for p in problem_results if p.accepted)
        rows_data.append((solved, penalty, team.team_id, team.name, problem_results))

    rows_data.sort(key=lambda item: (-item[0], item[1], str(item[2])))

    standing_rows: list[StandingRow] = []
    current_rank = 1
    for i, (solved, penalty, team_id, team_name, problem_results) in enumerate(rows_data):
        if i > 0:
            prev_solved = rows_data[i - 1][0]
            prev_penalty = rows_data[i - 1][1]
            if solved != prev_solved or penalty != prev_penalty:
                current_rank = i + 1

        standing_rows.append(
            StandingRow(
                rank=current_rank,
                team_id=team_id,
                team_name=team_name if include_team_names else None,
                solved=solved,
                penalty=penalty,
                problems=problem_results,
            )
        )

    return Standings(
        contest_id=contest.contest_id,
        frozen=frozen,
        rows=standing_rows,
        last_event_sequence=last_event_sequence,
    )
