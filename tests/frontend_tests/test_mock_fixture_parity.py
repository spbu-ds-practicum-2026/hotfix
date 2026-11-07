
import json
from pathlib import Path

import pytest

client_module = pytest.importorskip("app.api.client")

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "standings"


@pytest.fixture
def api_client():
    return client_module.ApiClient(use_mocks=True)


def _fixture(filename: str) -> dict:
    return json.loads((FIXTURES_DIR / filename).read_text(encoding="utf-8"))


def _expected_rows(fixture: dict) -> tuple:
    return tuple(
        (row["rank"], row["teamId"], row["solved"], row["penalty"])
        for row in fixture["rows"]
    )


def _actual_rows(standings) -> tuple:
    return tuple((r.rank, r.team_id, r.solved, r.penalty) for r in standings.rows)


@pytest.mark.asyncio
async def test_mock_standings_normal_matches_fixture(api_client):
    fixture = _fixture("standings-normal.json")

    standings = await api_client.get_standings(client_module.OPEN_CONTEST_ID)

    assert standings.frozen is False
    assert _actual_rows(standings) == _expected_rows(fixture)


@pytest.mark.asyncio
async def test_mock_standings_frozen_matches_fixture(api_client):
    fixture = _fixture("standings-frozen.json")

    standings = await api_client.get_standings(client_module.FROZEN_CONTEST_ID)

    assert standings.frozen is True
    assert _actual_rows(standings) == _expected_rows(fixture)


@pytest.mark.asyncio
async def test_mock_standings_tie_break_matches_fixture(api_client):
    fixture = _fixture("standings-tie-break.json")

    standings = await api_client.get_standings(client_module.TIEBREAK_CONTEST_ID)

    assert standings.frozen is False
    assert _actual_rows(standings) == _expected_rows(fixture)


@pytest.mark.asyncio
async def test_mock_standings_empty_state(api_client):
    standings = await api_client.get_standings(client_module.EMPTY_CONTEST_ID)

    assert standings.rows == []
    assert standings.frozen is False
