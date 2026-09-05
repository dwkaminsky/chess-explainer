"""Container-profile integration checks.

These tests are opt-in because the regular developer test suite intentionally
uses SQLite and does not require a running service. The Compose ``test``
service sets RUN_CONTAINER_INTEGRATION=1 and points DATABASE_URL at test-db.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from contextlib import suppress
from uuid import UUID, uuid4

import chess
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import create_engine as create_sync_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.exc import IntegrityError

from app.api import app
from app.config import get_settings
from app.candidates import CandidateResult
from app.db import create_engine
from app.facts.extract import build_factual_result
from app.models import Task, TaskStatus
from app.stockfish import MateResult, evaluate_fen
from app.tasks import claim_one, complete_task, create_task, recover_expired
from app.worker import Worker

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_CONTAINER_INTEGRATION") != "1",
    reason="container integration tests require the Compose test profile",
)

TEST_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
FACTUAL_FEN = "6k1/5ppp/8/8/3P4/8/5PPP/6K1 w - - 0 1"
CHECKMATE_FEN = "7k/6Q1/6K1/8/8/8/8/8 b - - 0 1"
EXPECTED_FACTS = {
    "material": {
        "white": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 4},
        "black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 3},
        "white_minus_black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 1},
    },
    "pawns": {
        "white": {"isolated": ["d4"], "doubled_files": {}, "passed": ["d4"]},
        "black": {"isolated": [], "doubled_files": {}, "passed": []},
    },
    "files": {
        "open": ["a", "b", "c", "e"],
        "semi_open": {"white": [], "black": ["d"]},
    },
}
EXPECTED_EXPLANATION = (
    "White has one more pawn than Black. White's d4-pawn is isolated and passed. "
    "The a-, b-, c-, and e-files are open; the d-file is semi-open for Black."
)
EXPECTED_FACTUAL_RESULT = {
    "version": 1,
    "facts": EXPECTED_FACTS,
    "explanation": EXPECTED_EXPLANATION,
}


def terminal_candidate_result(fen: str) -> dict[str, object]:
    return {
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
            "normalized_fen": fen,
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


def _run_alembic(*args: str, database_url: str | None = None) -> None:
    env = os.environ.copy()
    if database_url is not None:
        env["DATABASE_URL"] = database_url
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        check=True,
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
    )


def _admin_database_url(database_url: str) -> str:
    return make_url(database_url).set(database="postgres").render_as_string(hide_password=False)


@pytest_asyncio.fixture(autouse=True)
async def isolated_task_table():
    """Keep reruns and parallel PostgreSQL tests from sharing queue rows."""

    engine = create_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE TABLE tasks"))
    try:
        yield
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE TABLE tasks"))
        await engine.dispose()


@pytest_asyncio.fixture
async def postgres_factories():
    """Two independent sessions exercise PostgreSQL's row-lock protocol."""

    database_url = os.environ["DATABASE_URL"]
    engine_one = create_engine(database_url)
    engine_two = create_engine(database_url)
    factory_one = async_sessionmaker(engine_one, expire_on_commit=False, class_=AsyncSession)
    factory_two = async_sessionmaker(engine_two, expire_on_commit=False, class_=AsyncSession)
    try:
        yield factory_one, factory_two
    finally:
        await engine_one.dispose()
        await engine_two.dispose()


@pytest.mark.asyncio
async def test_postgres_concurrent_claim_has_one_winner(postgres_factories):
    factory_one, factory_two = postgres_factories
    async with factory_one() as session:
        task = await create_task(session, TEST_FEN, {"search_time_seconds": 0.2})
        await session.commit()
        task_id = task.id

    async with factory_one() as session_one, factory_two() as session_two:
        first, second = await asyncio.gather(
            claim_one(session_one, lease_seconds=15),
            claim_one(session_two, lease_seconds=15),
        )

    winners = [claim for claim in (first, second) if claim is not None]
    assert len(winners) == 1
    assert winners[0].id == task_id
    assert winners[0].attempts == 1


@pytest.mark.asyncio
async def test_postgres_stale_lease_cannot_complete_new_attempt(postgres_factories):
    factory_one, _factory_two = postgres_factories
    async with factory_one() as session:
        task = await create_task(session, TEST_FEN, {})
        await session.commit()
        old = await claim_one(session, lease_seconds=0.1)
        assert old is not None and old.lease_token is not None
        old_token = old.lease_token
        await asyncio.sleep(0.2)
        assert await recover_expired(session) == 1
        new = await claim_one(session, lease_seconds=15)
        assert new is not None and new.lease_token != old_token
        assert await complete_task(
            session,
            task.id,
            old_token,
            evaluation_cp=34,
            factual_result=EXPECTED_FACTUAL_RESULT,
            candidate_result=terminal_candidate_result(FACTUAL_FEN),
        ) is False


@pytest.mark.asyncio
async def test_real_stockfish_adapter_returns_usable_result():
    result = await asyncio.to_thread(
        evaluate_fen,
        CHECKMATE_FEN,
        {
            "engine_path": os.getenv("ENGINE_PATH", "/usr/games/stockfish"),
            "search_time_seconds": 0.2,
            "hard_attempt_timeout_seconds": 5.0,
            "engine_threads": 1,
            "engine_hash_mb": 64,
        },
    )
    assert isinstance(result, (int, MateResult))


@pytest.mark.asyncio
async def test_submit_and_poll_return_queued_state_with_null_candidate_fields():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=10.0) as client:
        response = await client.post("/tasks", json={"fen": FACTUAL_FEN})
        assert response.status_code == 202, response.text
        task_id = response.json()["task_id"]
        UUID(task_id)

        queued = await client.get(f"/tasks/{task_id}")
        assert queued.status_code == 200, queued.text
        assert queued.json() == {
            "task_id": task_id,
            "status": "queued",
            "evaluation": None,
            "facts": None,
            "explanation": None,
            "explanation_version": None,
            "candidate_moves": None,
            "candidate_analysis": None,
        }


@pytest.mark.asyncio
async def test_submit_to_completion_through_real_container_stack(postgres_factories):
    factory_one, _factory_two = postgres_factories
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=10.0) as client:
        response = await client.post("/tasks", json={"fen": FACTUAL_FEN})
        assert response.status_code == 202, response.text
        task_id = response.json()["task_id"]
        UUID(task_id)

        queued = await client.get(f"/tasks/{task_id}")
        assert queued.status_code == 200, queued.text
        assert queued.json() == {
            "task_id": task_id,
            "status": "queued",
            "evaluation": None,
            "facts": None,
            "explanation": None,
            "explanation_version": None,
            "candidate_moves": None,
            "candidate_analysis": None,
        }

        worker = Worker(evaluation_config=get_settings(), evaluator=evaluate_fen)
        assert await asyncio.to_thread(worker.process_once) is True

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            poll = await client.get(f"/tasks/{task_id}")
            assert poll.status_code == 200, poll.text
            body = poll.json()
            if body["status"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.25)
        else:
            pytest.fail("task did not reach a terminal state within 20 seconds")

        assert body["status"] == "completed", body
        assert body["facts"] == EXPECTED_FACTS
        assert body["explanation"] == EXPECTED_EXPLANATION
        assert body["explanation_version"] == 1
        assert body["candidate_moves"] is not None
        assert body["candidate_analysis"] is not None
        assert body["candidate_analysis"]["selection_policy"] == "last_complete_depth"
        assert body["candidate_analysis"]["root_side"] == "white"
        assert body["candidate_analysis"]["returned_count"] == min(
            3, sum(1 for _ in chess.Board(FACTUAL_FEN).legal_moves)
        )
        assert all(len(move["continuation"]) <= 6 for move in body["candidate_moves"])
        assert body["candidate_moves"][0]["evaluation"] == body["evaluation"]
        assert body["candidate_moves"][0]["mate"] == body.get("mate")
        root = chess.Board(FACTUAL_FEN)
        for move in body["candidate_moves"]:
            board = chess.Board(root.fen())
            for ply in move["continuation"]:
                uci = chess.Move.from_uci(ply["uci"])
                assert uci in board.legal_moves
                board.push(uci)
        first_move = body["candidate_moves"][0]
        assert "from_square" not in first_move["continuation"][0]["mover"]
        assert "to_square" not in first_move["continuation"][0]["mover"]
        rook_move = first_move["continuation"][0]["rook_move"]
        if rook_move is not None:
            assert "from_square" not in rook_move
            assert "to_square" not in rook_move

        async with factory_one() as session:
            row = await session.get(Task, UUID(task_id))
            assert row is not None
            assert row.factual_result == EXPECTED_FACTUAL_RESULT
            assert row.candidate_result is not None
            stored_candidate = CandidateResult.model_validate(row.candidate_result)
            assert stored_candidate.analysis.returned_count == len(body["candidate_moves"])
            assert stored_candidate.analysis.returned_count == body["candidate_analysis"]["returned_count"]
            assert body["candidate_moves"] == [
                move.model_dump(mode="json", by_alias=True) for move in stored_candidate.moves
            ]
            assert body["candidate_analysis"] == {
                "version": stored_candidate.version,
                **stored_candidate.analysis.model_dump(mode="json"),
            }


@pytest.mark.asyncio
async def test_postgres_fresh_install_applies_head_migrations():
    shared_database_url = os.environ["DATABASE_URL"]
    temp_database_name = f"chess_fresh_install_{uuid4().hex}"
    temp_database_url = make_url(shared_database_url).set(database=temp_database_name).render_as_string(
        hide_password=False
    )
    admin_engine = create_sync_engine(_admin_database_url(shared_database_url), isolation_level="AUTOCOMMIT")
    engine = create_engine(temp_database_url)
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{temp_database_name}" WITH (FORCE)'))
            connection.execute(text(f'CREATE DATABASE "{temp_database_name}"'))

        _run_alembic("upgrade", "head", database_url=temp_database_url)

        async with engine.begin() as connection:
            columns = await connection.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'tasks'
                    """
                )
            )
            column_names = {row[0] for row in columns}
            assert {"factual_result", "candidate_result", "evaluation_config"} <= column_names
            count = await connection.execute(text("SELECT count(*) FROM tasks"))
            assert count.scalar_one() == 0
    finally:
        await engine.dispose()
        with suppress(Exception):
            with admin_engine.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{temp_database_name}" WITH (FORCE)'))
        admin_engine.dispose()


@pytest.mark.asyncio
async def test_stale_completion_keeps_the_winning_factual_bundle(postgres_factories):
    factory_one, _factory_two = postgres_factories
    async with factory_one() as session:
        task = await create_task(session, FACTUAL_FEN, {})
        await session.commit()

        first = await claim_one(session, lease_seconds=0.1)
        assert first is not None and first.lease_token is not None
        stale_token = first.lease_token

        await asyncio.sleep(0.2)
        assert await recover_expired(session) == 1

        second = await claim_one(session, lease_seconds=15)
        assert second is not None and second.lease_token is not None

        winning_bundle = EXPECTED_FACTUAL_RESULT
        assert await complete_task(
            session,
            task.id,
            second.lease_token,
            evaluation_cp=34,
            factual_result=winning_bundle,
            candidate_result=terminal_candidate_result(FACTUAL_FEN),
        ) is True

        stale_bundle = build_factual_result(chess.Board(TEST_FEN)).model_dump(mode="json")
        assert await complete_task(
            session,
            task.id,
            stale_token,
            evaluation_cp=123,
            factual_result=stale_bundle,
            candidate_result=terminal_candidate_result(TEST_FEN),
        ) is False

        row = await session.get(Task, task.id)
        assert row is not None
        assert row.factual_result == winning_bundle
        assert row.candidate_result == terminal_candidate_result(FACTUAL_FEN)


@pytest.mark.asyncio
async def test_postgres_database_session_persistence_preserves_completed_rows_and_candidate_result():
    database_url = os.environ["DATABASE_URL"]
    engine = create_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as session:
            task = await create_task(session, FACTUAL_FEN, {})
            await session.commit()
            claimed = await claim_one(session, lease_seconds=15)
            assert claimed is not None and claimed.lease_token is not None
            assert await complete_task(
                session,
                task.id,
                claimed.lease_token,
                evaluation_cp=34,
                factual_result=EXPECTED_FACTUAL_RESULT,
                candidate_result=terminal_candidate_result(FACTUAL_FEN),
            ) is True

        await engine.dispose()
        engine = create_engine(database_url)
        async with async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)() as session:
            row = await session.get(Task, task.id)
            assert row is not None
            assert row.status == TaskStatus.COMPLETED.value
            assert row.factual_result == EXPECTED_FACTUAL_RESULT
            assert row.candidate_result == terminal_candidate_result(FACTUAL_FEN)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_candidate_result_constraints_reject_invalid_shapes(postgres_factories):
    factory_one, factory_two = postgres_factories

    async with factory_one() as session:
        task = await create_task(session, FACTUAL_FEN, {})
        await session.commit()
        with pytest.raises(IntegrityError):
            async with session.begin():
                await session.execute(
                    text("UPDATE tasks SET candidate_result = CAST(:candidate_result AS jsonb) WHERE id = :id"),
                    {"candidate_result": "{}", "id": task.id},
                )

    async with factory_two() as session:
        task = await create_task(session, FACTUAL_FEN, {})
        await session.commit()
        with pytest.raises(IntegrityError):
            async with session.begin():
                await session.execute(
                    text(
                        """
                        UPDATE tasks
                        SET status = 'completed',
                            evaluation_cp = 0,
                            finished_at = CURRENT_TIMESTAMP,
                            candidate_result = CAST(:candidate_result AS jsonb)
                        WHERE id = :id
                        """
                    ),
                    {"candidate_result": "[]", "id": task.id},
                )


@pytest.mark.asyncio
async def test_postgres_upgrade_preserves_old_completed_rows_without_candidate_result():
    shared_database_url = os.environ["DATABASE_URL"]
    temp_database_name = f"chess_preservation_{uuid4().hex}"
    temp_database_url = make_url(shared_database_url).set(database=temp_database_name).render_as_string(
        hide_password=False
    )
    admin_engine = create_sync_engine(_admin_database_url(shared_database_url), isolation_level="AUTOCOMMIT")
    engine = create_engine(temp_database_url)
    task_id = UUID("11111111-1111-1111-1111-111111111111")
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{temp_database_name}" WITH (FORCE)'))
            connection.execute(text(f'CREATE DATABASE "{temp_database_name}"'))

        _run_alembic("upgrade", "0002", database_url=temp_database_url)
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO tasks (
                        id,
                        fen,
                        status,
                        evaluation_cp,
                        factual_result,
                        mate_winner,
                        mate_moves,
                        attempts,
                        lease_token,
                        lease_expires_at,
                        error_code,
                        error_message,
                        engine_version,
                        evaluation_config,
                        created_at,
                        started_at,
                        finished_at
                    ) VALUES (
                        :id,
                        :fen,
                        :status,
                        :evaluation_cp,
                        CAST(:factual_result AS jsonb),
                        NULL,
                        NULL,
                        :attempts,
                        NULL,
                        NULL,
                        NULL,
                        NULL,
                        :engine_version,
                        CAST(:evaluation_config AS jsonb),
                        CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "id": task_id,
                    "fen": FACTUAL_FEN,
                    "status": "completed",
                    "evaluation_cp": 34,
                    "factual_result": json.dumps(EXPECTED_FACTUAL_RESULT),
                    "attempts": 1,
                    "engine_version": "stockfish-15.1-4",
                    "evaluation_config": json.dumps(
                        {
                            "search_time_seconds": 0.2,
                            "hard_attempt_timeout_seconds": 5.0,
                            "engine_threads": 1,
                            "engine_hash_mb": 64,
                            "engine_path": "/usr/games/stockfish",
                            "engine_version": "stockfish-15.1-4",
                        }
                    ),
                },
            )

        _run_alembic("upgrade", "head", database_url=temp_database_url)

        async with async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)() as session:
            row = await session.get(Task, task_id)
            assert row is not None
            assert row.status == TaskStatus.COMPLETED.value
            assert row.evaluation_cp == 34
            assert row.factual_result == EXPECTED_FACTUAL_RESULT
            assert row.mate_winner is None
            assert row.mate_moves is None
            assert row.error_code is None
            assert row.error_message is None
            assert row.engine_version == "stockfish-15.1-4"
            assert row.candidate_result is None
            assert row.evaluation_config == {
                "search_time_seconds": 0.2,
                "hard_attempt_timeout_seconds": 5.0,
                "engine_threads": 1,
                "engine_hash_mb": 64,
                "engine_path": "/usr/games/stockfish",
                "engine_version": "stockfish-15.1-4",
            }
    finally:
        await engine.dispose()
        with suppress(Exception):
            with admin_engine.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{temp_database_name}" WITH (FORCE)'))
        admin_engine.dispose()
