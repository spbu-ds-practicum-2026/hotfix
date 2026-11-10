from __future__ import annotations

from typing import Any
from uuid import UUID

from contracts.errors import contest_not_found
from domain.models import Contest, Standings
from domain.repository import DomainRepository, InMemoryDomainRepository
from domain.standings_service import StandingsService

try:
    from fastapi import APIRouter, Depends
except ImportError:
    class APIRouter:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def get(self, *args: Any, **kwargs: Any) -> Any:
            return lambda fn: fn

        def post(self, *args: Any, **kwargs: Any) -> Any:
            return lambda fn: fn

    def Depends(fn: Any) -> Any:
        return fn

router = APIRouter(prefix="/api/v1")

_active_standings_services: dict[UUID, StandingsService] = {}
_default_repository: DomainRepository = InMemoryDomainRepository()


def get_domain_repository() -> DomainRepository:
    return _default_repository


def set_domain_repository(repo: DomainRepository) -> None:
    global _default_repository
    _default_repository = repo
    _active_standings_services.clear()


def get_standings_service_for(contest: Contest, repo: DomainRepository) -> StandingsService:
    if contest.contest_id not in _active_standings_services:
        teams = repo.list_teams(contest.contest_id)
        problems = repo.list_problems(contest.contest_id)
        submissions = repo.list_submissions(contest.contest_id)
        _active_standings_services[contest.contest_id] = StandingsService(
            contest=contest,
            teams=teams,
            problems=problems,
            initial_attempts=submissions,
            is_frozen=contest.is_frozen,
        )
    else:
        service = _active_standings_services[contest.contest_id]
        for sub in repo.list_submissions(contest.contest_id):
            if sub.submission_id not in service.processed_submission_ids:
                service.attempts.append(sub)
                service.processed_submission_ids.add(sub.submission_id)
    return _active_standings_services[contest.contest_id]


@router.get("/contests")
def list_contests(repo: DomainRepository = Depends(get_domain_repository)) -> list[dict[str, Any]]:
    contests = repo.list_contests()
    return [c.to_dict() for c in contests]


@router.get("/contests/{contest_id}")
def get_contest_details(
    contest_id: UUID,
    repo: DomainRepository = Depends(get_domain_repository),
) -> dict[str, Any]:
    contest = repo.get_contest(contest_id)
    if contest is None:
        raise contest_not_found()
    return contest.to_dict()


@router.get("/contests/{contest_id}/teams")
def list_contest_teams(
    contest_id: UUID,
    repo: DomainRepository = Depends(get_domain_repository),
) -> list[dict[str, Any]]:
    contest = repo.get_contest(contest_id)
    if contest is None:
        raise contest_not_found()
    teams = repo.list_teams(contest_id)
    return [t.to_dict() for t in teams]


@router.get("/contests/{contest_id}/problems")
def list_contest_problems(
    contest_id: UUID,
    repo: DomainRepository = Depends(get_domain_repository),
) -> list[dict[str, Any]]:
    contest = repo.get_contest(contest_id)
    if contest is None:
        raise contest_not_found()
    problems = repo.list_problems(contest_id)
    return [p.to_dict() for p in problems]


@router.get("/contests/{contest_id}/standings")
def get_contest_standings(
    contest_id: UUID,
    repo: DomainRepository = Depends(get_domain_repository),
) -> dict[str, Any]:
    contest = repo.get_contest(contest_id)
    if contest is None:
        raise contest_not_found()

    saved_standings = repo.get_standings(contest_id)
    if (
        saved_standings is not None
        and saved_standings.rows
        and contest_id not in _active_standings_services
    ):
        return saved_standings.to_dict()

    service = get_standings_service_for(contest, repo)
    standings = service.get_standings()
    repo.save_standings(standings)
    return standings.to_dict()


@router.post("/contests/{contest_id}/freeze")
def freeze_contest_standings(
    contest_id: UUID,
    repo: DomainRepository = Depends(get_domain_repository),
) -> dict[str, Any]:
    contest = repo.get_contest(contest_id)
    if contest is None:
        raise contest_not_found()

    service = get_standings_service_for(contest, repo)
    frozen_standings = service.freeze()
    repo.set_contest_freeze(contest_id, is_frozen=True)
    repo.save_standings(frozen_standings)
    return frozen_standings.to_dict()


@router.post("/contests/{contest_id}/unfreeze")
def unfreeze_contest_standings(
    contest_id: UUID,
    repo: DomainRepository = Depends(get_domain_repository),
) -> dict[str, Any]:
    contest = repo.get_contest(contest_id)
    if contest is None:
        raise contest_not_found()

    service = get_standings_service_for(contest, repo)
    unfrozen_standings = service.unfreeze()
    repo.set_contest_freeze(contest_id, is_frozen=False)
    repo.save_standings(unfrozen_standings)
    return unfrozen_standings.to_dict()
