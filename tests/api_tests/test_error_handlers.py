"""Единый формат ошибок API (services/api/src/api/error_handlers.py).

Нужны fastapi и httpx (pip install -r services/frontend/requirements.txt); без них тесты пропускаются.
"""

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")
handlers = pytest.importorskip("api.error_handlers")
errors = pytest.importorskip("contracts.errors")

from fastapi.testclient import TestClient
from pydantic import BaseModel


class Item(BaseModel):
    name: str


def _client():
    application = fastapi.FastAPI()
    handlers.register_error_handlers(application)

    @application.post("/items")
    async def create_item(item: Item):
        return {"name": item.name}

    @application.get("/missing")
    async def missing():
        raise errors.contest_not_found()

    @application.get("/down")
    async def down():
        raise errors.temporarily_unavailable()

    return TestClient(application)


def test_api_error_is_rendered_in_common_format():
    response = _client().get("/missing")
    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "CONTEST_NOT_FOUND",
            "message": "Contest not found",
            "details": {},
        }
    }


def test_service_unavailable_maps_to_503():
    response = _client().get("/down")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "TEMPORARILY_UNAVAILABLE"


def test_validation_error_is_400_with_field_name():
    response = _client().post("/items", json={})
    assert response.status_code == 400
    body = response.json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"] == {"field": "name"}


def test_valid_request_passes_through():
    response = _client().post("/items", json={"name": "x"})
    assert response.status_code == 200
    assert response.json() == {"name": "x"}
