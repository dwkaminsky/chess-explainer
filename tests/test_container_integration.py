"""Container-profile integration checks.

These tests are opt-in because the regular developer test suite intentionally
uses SQLite and does not require a running service. The Compose ``test``
service sets RUN_CONTAINER_INTEGRATION=1 and points DATABASE_URL at test-db.
The end-to-end test drives the ASGI API and one real Worker attempt in-process.
"""

from __future__ import annotations

import asyncio
import os
import time
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import app
from app.config import get_settings
from app.db import create_engine
from app.stockfish import MateResult, evaluate_fen
from app.tasks import claim_one, complete_task, create_task, recover_expired
from app.worker import Worker

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_CONTAINER_INTEGRATION") != "1",
    reason="container integration tests require the Compose test profile",
)

TEST_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


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
        assert await complete_task(session, task.id, old_token, evaluation_cp=34) is False


@pytest.mark.asyncio
async def test_real_stockfish_adapter_returns_usable_result():
    result = await asyncio.to_thread(
        evaluate_fen,
        TEST_FEN,
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
async def test_submit_to_completion_through_real_container_stack():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=10.0) as client:
        response = await client.post("/tasks", json={"fen": TEST_FEN})
        assert response.status_code == 202, response.text
        task_id = response.json()["task_id"]
        UUID(task_id)

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
        if body["evaluation"] is None:
            assert body.get("mate", {}).get("winner") in {"white", "black"}
        else:
            assert isinstance(body["evaluation"], (int, float))
