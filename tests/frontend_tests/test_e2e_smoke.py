
import re

import pytest

pytest.importorskip("fastapi")
main = pytest.importorskip("app.main")

from fastapi.testclient import TestClient

OPEN_CONTEST_ID = "11111111-1111-1111-1111-111111111111"


def test_full_user_journey_smoke():
    client = TestClient(main.app)

    # 1. список контестов
    contests_page = client.get("/")
    assert contests_page.status_code == 200
    assert OPEN_CONTEST_ID in contests_page.text

    # 2. контест: задачи и выбор команды
    contest_page = client.get(f"/contests/{OPEN_CONTEST_ID}")
    assert contest_page.status_code == 200
    problem_id = re.search(
        r'value="([0-9a-f-]{36})">A — Hello', contest_page.text
    ).group(1)
    team_id = re.search(r'value="(aaaa1111-[0-9a-f-]{27})"', contest_page.text).group(1)
    assert problem_id and team_id

    # 3. отправка решения
    submit_response = client.post(
        "/submit",
        data={
            "contest_id": OPEN_CONTEST_ID,
            "team_id": team_id,
            "problem_id": problem_id,
            "language": "cpp",
            "source_code": "#include <iostream>\nint main() { std::cout << 1; }",
        },
        follow_redirects=False,
    )
    assert submit_response.status_code == 303
    submission_url = submit_response.headers["location"]
    assert submission_url.startswith("/submissions/")

    # 4. статус и вердикт посылки
    status_page = client.get(submission_url)
    assert status_page.status_code == 200
    assert "COMPLETED" in status_page.text
    assert "ACCEPTED" in status_page.text

    # 5. standings
    standings_page = client.get(f"/contests/{OPEN_CONTEST_ID}/standings")
    assert standings_page.status_code == 200
    assert "Team Alpha" in standings_page.text
    assert "Team Beta" in standings_page.text
