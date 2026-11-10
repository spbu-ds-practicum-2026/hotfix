from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime
from typing import Any
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


class DomainRepository(ABC):
    @abstractmethod
    def get_contest(self, contest_id: UUID) -> Contest | None:
        pass

    @abstractmethod
    def list_contests(self) -> list[Contest]:
        pass

    @abstractmethod
    def list_teams(self, contest_id: UUID) -> list[Team]:
        pass

    @abstractmethod
    def list_problems(self, contest_id: UUID) -> list[Problem]:
        pass

    @abstractmethod
    def list_submissions(self, contest_id: UUID) -> list[SubmissionAttempt]:
        pass

    @abstractmethod
    def get_standings(self, contest_id: UUID) -> Standings | None:
        pass

    @abstractmethod
    def save_standings(self, standings: Standings) -> None:
        pass

    @abstractmethod
    def set_contest_freeze(self, contest_id: UUID, is_frozen: bool) -> None:
        pass


class InMemoryDomainRepository(DomainRepository):
    def __init__(
        self,
        contests: list[Contest] | None = None,
        teams: list[Team] | None = None,
        problems: list[Problem] | None = None,
        standings: list[Standings] | None = None,
        submissions: list[SubmissionAttempt] | None = None,
    ) -> None:
        self._contests: dict[UUID, Contest] = {c.contest_id: c for c in (contests or [])}
        self._teams: dict[UUID, list[Team]] = {}
        for t in teams or []:
            self._teams.setdefault(t.contest_id, []).append(t)
        self._problems: dict[UUID, list[Problem]] = {}
        for p in problems or []:
            self._problems.setdefault(p.contest_id, []).append(p)
        self._submissions: dict[UUID, list[SubmissionAttempt]] = {}
        for sub in submissions or []:
            self._submissions.setdefault(sub.contest_id, []).append(sub)
        self._standings: dict[UUID, Standings] = {s.contest_id: s for s in (standings or [])}

    def add_contest(self, contest: Contest) -> None:
        self._contests[contest.contest_id] = contest

    def add_team(self, team: Team) -> None:
        self._teams.setdefault(team.contest_id, []).append(team)

    def add_problem(self, problem: Problem) -> None:
        self._problems.setdefault(problem.contest_id, []).append(problem)

    def add_submission(self, submission: SubmissionAttempt) -> None:
        self._submissions.setdefault(submission.contest_id, []).append(submission)

    def get_contest(self, contest_id: UUID) -> Contest | None:
        return self._contests.get(contest_id)

    def list_contests(self) -> list[Contest]:
        return list(self._contests.values())

    def list_teams(self, contest_id: UUID) -> list[Team]:
        return list(self._teams.get(contest_id, []))

    def list_problems(self, contest_id: UUID) -> list[Problem]:
        return list(self._problems.get(contest_id, []))

    def list_submissions(self, contest_id: UUID) -> list[SubmissionAttempt]:
        return list(self._submissions.get(contest_id, []))

    def get_standings(self, contest_id: UUID) -> Standings | None:
        return self._standings.get(contest_id)

    def save_standings(self, standings: Standings) -> None:
        self._standings[standings.contest_id] = standings

    def set_contest_freeze(self, contest_id: UUID, is_frozen: bool) -> None:
        contest = self._contests.get(contest_id)
        if contest is not None:
            self._contests[contest_id] = Contest(
                contest_id=contest.contest_id,
                name=contest.name,
                starts_at=contest.starts_at,
                ends_at=contest.ends_at,
                is_frozen=is_frozen,
            )
        standings = self._standings.get(contest_id)
        if standings is not None:
            self._standings[contest_id] = Standings(
                contest_id=standings.contest_id,
                frozen=is_frozen,
                rows=standings.rows,
                last_event_sequence=standings.last_event_sequence,
            )


class SqlDomainRepository(DomainRepository):
    def __init__(self, engine_or_connection: Any) -> None:
        self.engine = engine_or_connection

    def get_contest(self, contest_id: UUID) -> Contest | None:
        from sqlalchemy import text

        sql = text(
            "SELECT contest_id, name, starts_at, ends_at, is_frozen "
            "FROM contests WHERE contest_id = :cid"
        )
        with self.engine.connect() as conn:
            row = conn.execute(sql, {"cid": contest_id}).mappings().first()
            if row is None:
                return None
            return Contest(
                contest_id=row["contest_id"],
                name=row["name"],
                starts_at=row["starts_at"],
                ends_at=row["ends_at"],
                is_frozen=row["is_frozen"],
            )

    def list_contests(self) -> list[Contest]:
        from sqlalchemy import text

        sql = text("SELECT contest_id, name, starts_at, ends_at, is_frozen FROM contests ORDER BY starts_at DESC")
        with self.engine.connect() as conn:
            rows = conn.execute(sql).mappings().all()
            return [
                Contest(
                    contest_id=r["contest_id"],
                    name=r["name"],
                    starts_at=r["starts_at"],
                    ends_at=r["ends_at"],
                    is_frozen=r["is_frozen"],
                )
                for r in rows
            ]

    def list_teams(self, contest_id: UUID) -> list[Team]:
        from sqlalchemy import text

        sql = text("SELECT team_id, contest_id, name FROM teams WHERE contest_id = :cid ORDER BY name")
        with self.engine.connect() as conn:
            rows = conn.execute(sql, {"cid": contest_id}).mappings().all()
            return [
                Team(
                    team_id=r["team_id"],
                    contest_id=r["contest_id"],
                    name=r["name"],
                )
                for r in rows
            ]

    def list_problems(self, contest_id: UUID) -> list[Problem]:
        from sqlalchemy import text

        sql = text(
            "SELECT problem_id, contest_id, code, title, time_limit_ms, memory_limit_kb, test_config_reference "
            "FROM problems WHERE contest_id = :cid ORDER BY code"
        )
        with self.engine.connect() as conn:
            rows = conn.execute(sql, {"cid": contest_id}).mappings().all()
            return [
                Problem(
                    problem_id=r["problem_id"],
                    contest_id=r["contest_id"],
                    code=r["code"],
                    title=r["title"],
                    time_limit_ms=r["time_limit_ms"],
                    memory_limit_kb=r["memory_limit_kb"],
                    test_config_reference=r["test_config_reference"],
                )
                for r in rows
            ]

    def list_submissions(self, contest_id: UUID) -> list[SubmissionAttempt]:
        from sqlalchemy import text

        sql = text(
            "SELECT submission_id, contest_id, team_id, problem_id, status, verdict, created_at "
            "FROM submissions WHERE contest_id = :cid ORDER BY created_at"
        )
        with self.engine.connect() as conn:
            rows = conn.execute(sql, {"cid": contest_id}).mappings().all()
            return [
                SubmissionAttempt(
                    submission_id=r["submission_id"],
                    contest_id=r["contest_id"],
                    team_id=r["team_id"],
                    problem_id=r["problem_id"],
                    status=SubmissionStatus(r["status"]),
                    verdict=Verdict(r["verdict"]) if r["verdict"] is not None else None,
                    created_at=r["created_at"],
                )
                for r in rows
            ]

    def get_standings(self, contest_id: UUID) -> Standings | None:
        import json
        from sqlalchemy import text

        sql = text(
            "SELECT contest_id, is_frozen, rows, last_event_sequence, updated_at "
            "FROM standings WHERE contest_id = :cid"
        )
        with self.engine.connect() as conn:
            row = conn.execute(sql, {"cid": contest_id}).mappings().first()
            if row is None:
                return None

            rows_data = row["rows"]
            if isinstance(rows_data, str):
                rows_data = json.loads(rows_data)

            from domain.models import ProblemResult, StandingRow

            parsed_rows = [
                StandingRow(
                    rank=r["rank"],
                    team_id=UUID(r["teamId"]),
                    team_name=r.get("teamName"),
                    solved=r["solved"],
                    penalty=r["penalty"],
                    problems=[
                        ProblemResult(
                            problem_id=UUID(p["problemId"]),
                            attempts=p["attempts"],
                            accepted=p["accepted"],
                            accepted_at=(
                                datetime.fromisoformat(p["acceptedAt"].replace("Z", "+00:00"))
                                if p.get("acceptedAt")
                                else None
                            ),
                            penalty=p["penalty"],
                        )
                        for p in r.get("problems", [])
                    ],
                )
                for r in rows_data
            ]
            return Standings(
                contest_id=row["contest_id"],
                frozen=row["is_frozen"],
                rows=parsed_rows,
                last_event_sequence=row["last_event_sequence"],
                updated_at=row["updated_at"],
            )

    def save_standings(self, standings: Standings) -> None:
        import json
        from sqlalchemy import text

        sql = text(
            """
            INSERT INTO standings (contest_id, is_frozen, rows, last_event_sequence, updated_at)
            VALUES (:cid, :frozen, CAST(:rows AS jsonb), :seq, now())
            ON CONFLICT (contest_id) DO UPDATE SET
                is_frozen = EXCLUDED.is_frozen,
                rows = EXCLUDED.rows,
                last_event_sequence = EXCLUDED.last_event_sequence,
                updated_at = now()
            """
        )
        rows_json = json.dumps([r.to_dict() for r in standings.rows])
        with self.engine.begin() as conn:
            conn.execute(
                sql,
                {
                    "cid": standings.contest_id,
                    "frozen": standings.frozen,
                    "rows": rows_json,
                    "seq": standings.last_event_sequence,
                },
            )

    def set_contest_freeze(self, contest_id: UUID, is_frozen: bool) -> None:
        from sqlalchemy import text

        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE contests SET is_frozen = :frozen WHERE contest_id = :cid"),
                {"cid": contest_id, "frozen": is_frozen},
            )
            conn.execute(
                text("UPDATE standings SET is_frozen = :frozen WHERE contest_id = :cid"),
                {"cid": contest_id, "frozen": is_frozen},
            )


class ShardedDomainRepository(DomainRepository):
    def __init__(
        self,
        shards: dict[int, DomainRepository],
        shard_selector: Callable[[UUID], int] | None = None,
    ) -> None:
        if not shards:
            raise ValueError("At least one shard repository is required")
        self._shards = dict(shards)
        if shard_selector is None:
            try:
                from storage.shard_router.ring import shard_index_for_contest

                self._shard_selector: Callable[[UUID], int] = shard_index_for_contest
            except ImportError:
                self._shard_selector = lambda cid: int(cid) % len(self._shards)
        else:
            self._shard_selector = shard_selector

    def set_shard_selector(self, shard_selector: Callable[[UUID], int]) -> None:
        self._shard_selector = shard_selector

    def _repo_for_contest(self, contest_id: UUID) -> DomainRepository:
        primary_index = self._shard_selector(contest_id)
        primary = self._shards.get(primary_index)
        if primary is not None and primary.get_contest(contest_id) is not None:
            return primary
        for idx, repo in self._shards.items():
            if idx != primary_index and repo.get_contest(contest_id) is not None:
                return repo
        if primary is not None:
            return primary
        return next(iter(self._shards.values()))

    def get_contest(self, contest_id: UUID) -> Contest | None:
        return self._repo_for_contest(contest_id).get_contest(contest_id)

    def list_contests(self) -> list[Contest]:
        seen: dict[UUID, Contest] = {}
        for repo in self._shards.values():
            for contest in repo.list_contests():
                owner = self._repo_for_contest(contest.contest_id)
                owner_contest = owner.get_contest(contest.contest_id)
                seen[contest.contest_id] = owner_contest or contest
        return sorted(seen.values(), key=lambda c: c.starts_at, reverse=True)

    def list_teams(self, contest_id: UUID) -> list[Team]:
        return self._repo_for_contest(contest_id).list_teams(contest_id)

    def list_problems(self, contest_id: UUID) -> list[Problem]:
        return self._repo_for_contest(contest_id).list_problems(contest_id)

    def list_submissions(self, contest_id: UUID) -> list[SubmissionAttempt]:
        return self._repo_for_contest(contest_id).list_submissions(contest_id)

    def get_standings(self, contest_id: UUID) -> Standings | None:
        return self._repo_for_contest(contest_id).get_standings(contest_id)

    def save_standings(self, standings: Standings) -> None:
        self._repo_for_contest(standings.contest_id).save_standings(standings)

    def set_contest_freeze(self, contest_id: UUID, is_frozen: bool) -> None:
        self._repo_for_contest(contest_id).set_contest_freeze(contest_id, is_frozen)
