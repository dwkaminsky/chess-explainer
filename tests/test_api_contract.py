import asyncio
import json
from pathlib import Path
from uuid import UUID

import chess
import pytest
from fastapi.testclient import TestClient

from app import api
from app.candidates import CandidateResult, validate_candidate_result
import app.candidates.build as candidate_build
import app.candidates.replay as candidate_replay
import app.explanations.render as factual_render
import app.facts.extract as facts_extract
from app.facts.models import FactualResult
from app.fen import normalize_fen
import app.stockfish as stockfish_module

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
FIXTURE = json.loads((Path(__file__).with_name("fixtures") / "candidate_public_example.json").read_text())
CANONICAL_FEN = "rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 2"
FACTUAL_RESULT = FIXTURE["factual_result"]

EMPTY_CANDIDATE_RESULT = {
    "version": 1,
    "analysis": {
        "facts_version": 1,
        "root_side": "white",
        "requested_count": 1,
        "returned_count": 0,
        "snapshot_depth": None,
        "search_budget_ms": 1,
        "max_continuation_plies": 6,
        "selection_policy": "terminal_position",
    },
    "moves": [],
    "provenance": {
        "normalized_fen": START,
        "engine_build": "stockfish-15.1-4",
        "network_hash": None,
        "options": {},
        "selected_depth": None,
        "raw_scores": [],
        "original_pv_lengths": [],
        "elapsed_attempt_ms": 0,
        "elapsed_search_ms": 0,
        "elapsed_replay_ms": 0,
        "elapsed_render_ms": 0,
        "incomplete_groups": 0,
        "bound_only_groups": 0,
        "duplicate_root_groups": 0,
        "inconsistent_groups": 0,
    },
}

EMPTY_CANDIDATE_ANALYSIS = {
    "version": 1,
    "facts_version": 1,
    "root_side": "white",
    "requested_count": 1,
    "returned_count": 0,
    "snapshot_depth": None,
    "search_budget_ms": 1,
    "max_continuation_plies": 6,
    "selection_policy": "terminal_position",
}


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


def _nonempty_candidate_result() -> dict[str, object]:
    return FIXTURE["candidate_result"]


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
        "facts": None,
        "explanation": None,
        "explanation_version": None,
        "candidate_moves": None,
        "candidate_analysis": None,
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
            "factual_result": FACTUAL_RESULT,
            "candidate_result": EMPTY_CANDIDATE_RESULT,
        },
        UUID("00000000-0000-0000-0000-000000000003"): {
            "id": UUID("00000000-0000-0000-0000-000000000003"),
            "status": "failed",
            "error_code": "ENGINE_TIMEOUT",
            "error_message": "timed out",
        },
        UUID("00000000-0000-0000-0000-000000000004"): {
            "id": UUID("00000000-0000-0000-0000-000000000004"),
            "status": "completed",
            "evaluation_cp": 34,
            "factual_result": FACTUAL_RESULT,
            "candidate_result": EMPTY_CANDIDATE_RESULT,
        },
        UUID("00000000-0000-0000-0000-000000000007"): {
            "id": UUID("00000000-0000-0000-0000-000000000007"),
            "status": "running",
            "candidate_result": None,
        },
        UUID("00000000-0000-0000-0000-000000000005"): {
            "id": UUID("00000000-0000-0000-0000-000000000005"),
            "status": "completed",
            "evaluation_cp": 123,
            "factual_result": {"version": 1, "facts": "bad", "explanation": "still bad"},
        },
        UUID("00000000-0000-0000-0000-000000000006"): {
            "id": UUID("00000000-0000-0000-0000-000000000006"),
            "status": "completed",
            "evaluation_cp": 123,
            "factual_result": FACTUAL_RESULT,
            "candidate_result": {"version": 1, "moves": [], "analysis": "bad", "provenance": {}},
        },
    }
    monkeypatch.setattr(api, "get_task", lambda session, task_id: rows.get(task_id))
    first = client.get("/tasks/00000000-0000-0000-0000-000000000001").json()
    assert first == {
        "task_id": "00000000-0000-0000-0000-000000000001",
        "status": "completed",
        "evaluation": 0.34,
        "facts": None,
        "explanation": None,
        "explanation_version": None,
        "candidate_moves": None,
        "candidate_analysis": None,
    }
    mate = client.get("/tasks/00000000-0000-0000-0000-000000000002").json()
    assert mate == {
        "task_id": "00000000-0000-0000-0000-000000000002",
        "status": "completed",
        "evaluation": None,
        "mate": {"winner": "white", "moves": 3},
        "facts": FACTUAL_RESULT["facts"],
        "explanation": FACTUAL_RESULT["explanation"],
        "explanation_version": 1,
        "candidate_moves": [],
        "candidate_analysis": EMPTY_CANDIDATE_ANALYSIS,
    }
    failed = client.get("/tasks/00000000-0000-0000-0000-000000000003").json()
    assert failed == {
        "task_id": "00000000-0000-0000-0000-000000000003",
        "status": "failed",
        "evaluation": None,
        "facts": None,
        "explanation": None,
        "explanation_version": None,
        "candidate_moves": None,
        "candidate_analysis": None,
        "error": {"code": "ENGINE_TIMEOUT", "message": "timed out"},
    }
    factual = client.get("/tasks/00000000-0000-0000-0000-000000000004").json()
    assert factual == {
        "task_id": "00000000-0000-0000-0000-000000000004",
        "status": "completed",
        "evaluation": 0.34,
        "facts": FACTUAL_RESULT["facts"],
        "explanation": FACTUAL_RESULT["explanation"],
        "explanation_version": 1,
        "candidate_moves": [],
        "candidate_analysis": EMPTY_CANDIDATE_ANALYSIS,
    }
    running = client.get("/tasks/00000000-0000-0000-0000-000000000007").json()
    assert running == {
        "task_id": "00000000-0000-0000-0000-000000000007",
        "status": "running",
        "evaluation": None,
        "facts": None,
        "explanation": None,
        "explanation_version": None,
        "candidate_moves": None,
        "candidate_analysis": None,
    }
    assert client.get("/tasks/00000000-0000-0000-0000-000000000005").status_code == 503
    assert client.get("/tasks/00000000-0000-0000-0000-000000000006").status_code == 503


def test_canonical_candidate_fixture_validates_and_maps(client, monkeypatch):
    root_fen = normalize_fen(CANONICAL_FEN)
    assert root_fen == CANONICAL_FEN
    board = chess.Board(root_fen)
    factual_result = FactualResult.model_validate(
        {
            "version": 1,
            "facts": FIXTURE["public_response"]["facts"],
            "explanation": FIXTURE["public_response"]["explanation"],
        }
    )
    candidate_result = CandidateResult.model_validate(FIXTURE["candidate_result"])
    assert candidate_result.model_dump(mode="json", by_alias=True) == FIXTURE["candidate_result"]
    assert validate_candidate_result(candidate_result, board) == candidate_result
    assert factual_result.model_dump(mode="json") == FACTUAL_RESULT

    rows = {
        UUID("00000000-0000-0000-0000-000000000008"): {
            "id": UUID("00000000-0000-0000-0000-000000000008"),
            "status": "completed",
            "evaluation_cp": 34,
            "factual_result": FIXTURE["factual_result"],
            "candidate_result": candidate_result.model_dump(mode="json", by_alias=True),
        }
    }
    monkeypatch.setattr(api, "get_task", lambda session, task_id: rows.get(task_id))
    body = client.get("/tasks/00000000-0000-0000-0000-000000000008").json()
    assert body == FIXTURE["public_response"]


def test_completed_poll_does_not_recompute_facts_or_candidates(client, monkeypatch):
    rows = {
        UUID("00000000-0000-0000-0000-000000000009"): {
            "id": UUID("00000000-0000-0000-0000-000000000009"),
            "status": "completed",
            "evaluation_cp": 34,
            "factual_result": FACTUAL_RESULT,
            "candidate_result": _nonempty_candidate_result(),
        }
    }
    monkeypatch.setattr(api, "get_task", lambda session, task_id: rows.get(task_id))

    def boom(*args, **kwargs):
        raise AssertionError("GET must not recompute factual or candidate data")

    monkeypatch.setattr(facts_extract, "build_factual_result", boom)
    monkeypatch.setattr(factual_render, "render_factual_explanation", boom)
    monkeypatch.setattr(candidate_build, "build_candidate_result", boom)
    monkeypatch.setattr(candidate_replay, "replay_candidate", boom)
    monkeypatch.setattr(stockfish_module, "evaluate_fen", boom)

    body = client.get("/tasks/00000000-0000-0000-0000-000000000009").json()
    assert body["evaluation"] == 0.34
    assert body["facts"] == FACTUAL_RESULT["facts"]
    assert body["explanation"] == FACTUAL_RESULT["explanation"]
    assert body["candidate_moves"] == FIXTURE["public_response"]["candidate_moves"]
