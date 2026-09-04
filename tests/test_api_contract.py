import asyncio
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app import api

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


@pytest.fixture()
def client(monkeypatch):
    rows = {}

    class DummySession:
        def commit(self):
            return None

    def fake_create(session, fen, evaluation_config, *, task_id):
        rows[task_id] = {"id": task_id, "fen": fen, "status": "queued"}

    def fake_get(session, task_id):
        return rows.get(task_id)

    monkeypatch.setattr(api, "create_task", fake_create)
    monkeypatch.setattr(api, "get_task", fake_get)
    api.app.dependency_overrides[api.get_session] = lambda: DummySession()
    yield TestClient(api.app)
    api.app.dependency_overrides.clear()


def test_submit_returns_202_location_and_queued_poll(client):
    response = client.post("/tasks", json={"fen": START})
    assert response.status_code == 202
    task_id = UUID(response.json()["task_id"])
    assert response.headers["location"] == f"/tasks/{task_id}"

    polled = client.get(f"/tasks/{task_id}")
    assert polled.status_code == 200
    assert polled.json() == {
        "task_id": str(task_id),
        "status": "queued",
        "evaluation": None,
    }
    assert polled.headers["cache-control"] == "no-store"


def test_extra_fields_and_oversized_body_are_rejected_without_create(client):
    extra = client.post("/tasks", json={"fen": START, "options": {"depth": 20}})
    assert extra.status_code == 422

    oversized = client.post("/tasks", content=b"{" + b"x" * 4096 + b"}")
    assert oversized.status_code == 413


def test_database_failure_is_not_acknowledged(client, monkeypatch):
    def failing_create(*args, **kwargs):
        raise RuntimeError("database down")

    monkeypatch.setattr(api, "create_task", failing_create)
    response = client.post("/tasks", json={"fen": START})
    assert response.status_code == 503


def test_commit_failure_rolls_back_and_is_not_acknowledged(client, monkeypatch):
    class FailingSession:
        rolled_back = False

        async def commit(self):
            raise RuntimeError("commit failed")

        async def rollback(self):
            self.rolled_back = True

    session = FailingSession()
    api.app.dependency_overrides[api.get_session] = lambda: session
    monkeypatch.setattr(
        api,
        "create_task",
        lambda session, fen, evaluation_config, *, task_id: None,
    )
    response = client.post("/tasks", json={"fen": START})
    assert response.status_code == 503
    assert session.rolled_back is True


def test_malformed_and_unknown_task_ids(client):
    assert client.get("/tasks/not-a-uuid").status_code == 422
    unknown = client.get("/tasks/00000000-0000-0000-0000-000000000000")
    assert unknown.status_code == 404
    assert unknown.headers["cache-control"] == "no-store"


def test_only_the_two_public_routes_are_exposed(client):
    routes = {
        (route.path, method)
        for route in api.app.routes
        for method in getattr(route, "methods", set())
    }
    assert routes == {("/tasks", "POST"), ("/tasks/{task_id}", "GET")}
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_chunked_body_without_content_length_is_limited():
    messages = [
        {"type": "http.request", "body": b"x" * 2048, "more_body": True},
        {"type": "http.request", "body": b"y" * 2049, "more_body": False},
    ]
    sent = []

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "path": "/tasks",
        "raw_path": b"/tasks",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 1),
    }
    asyncio.run(api.app(scope, receive, send))
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413


def test_completed_score_mate_and_failure_shapes(client, monkeypatch):
    rows = {
        UUID("00000000-0000-0000-0000-000000000001"): {
            "id": UUID("00000000-0000-0000-0000-000000000001"),
            "status": "completed",
            "evaluation_cp": 34,
        },
        UUID("00000000-0000-0000-0000-000000000002"): {
            "id": UUID("00000000-0000-0000-0000-000000000002"),
            "status": "completed",
            "mate_winner": "white",
            "mate_moves": 3,
        },
        UUID("00000000-0000-0000-0000-000000000003"): {
            "id": UUID("00000000-0000-0000-0000-000000000003"),
            "status": "failed",
            "error_code": "ENGINE_TIMEOUT",
            "error_message": "timed out",
        },
    }
    monkeypatch.setattr(api, "get_task", lambda session, task_id: rows.get(task_id))
    assert client.get("/tasks/00000000-0000-0000-0000-000000000001").json()["evaluation"] == 0.34
    mate = client.get("/tasks/00000000-0000-0000-0000-000000000002").json()
    assert mate["evaluation"] is None and mate["mate"] == {"winner": "white", "moves": 3}
    failed = client.get("/tasks/00000000-0000-0000-0000-000000000003").json()
    assert failed["error"]["code"] == "ENGINE_TIMEOUT"
