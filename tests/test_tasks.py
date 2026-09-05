from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import TaskStatus
from app.tasks import (
    claim_one,
    complete_task,
    create_task,
    fail_or_retry,
    get_task,
    recover_expired,
)


@pytest.mark.asyncio
async def test_create_and_claim_are_durable_state_transitions(db_session):
    task = await create_task(db_session, "valid fen", {"depth": 1})
    await db_session.commit()
    claimed = await claim_one(db_session, lease_seconds=30)

    assert claimed is not None
    assert claimed.id == task.id
    assert claimed.status == TaskStatus.RUNNING.value
    assert claimed.attempts == 1
    assert claimed.lease_token is not None
    assert claimed.lease_expires_at is not None


@pytest.mark.asyncio
async def test_stale_completion_is_rejected(db_session):
    task = await create_task(db_session, "valid fen", {})
    await db_session.commit()
    claimed = await claim_one(db_session)
    assert claimed is not None
    assert claimed.lease_token is not None

    # Simulate another worker taking the row after the old lease expired.
    claimed.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()
    assert await complete_task(db_session, task.id, claimed.lease_token, evaluation_cp=34) is False
    current = await get_task(db_session, task.id)
    assert current is not None and current.status == TaskStatus.RUNNING.value


@pytest.mark.asyncio
async def test_failure_requeues_once_then_is_terminal(db_session):
    task = await create_task(db_session, "valid fen", {})
    await db_session.commit()
    first = await claim_one(db_session)
    assert first is not None and first.lease_token is not None
    token = first.lease_token
    assert await fail_or_retry(
        db_session, task.id, token, error_code="ENGINE_TIMEOUT", error_message="timed out"
    ) == "requeued"

    second = await claim_one(db_session)
    assert second is not None and second.lease_token is not None
    outcome = await fail_or_retry(
        db_session,
        task.id,
        second.lease_token,
        error_code="ENGINE_TIMEOUT",
        error_message="timed out",
    )
    assert outcome == "failed"
    current = await get_task(db_session, task.id)
    assert current is not None
    assert current.status == TaskStatus.FAILED.value
    assert current.error_code == "ENGINE_TIMEOUT"
    assert current.lease_token is None


@pytest.mark.asyncio
async def test_complete_supports_ordinary_and_mate_results(db_session):
    ordinary = await create_task(db_session, "fen one", {})
    await db_session.commit()

    first = await claim_one(db_session)
    assert first is not None and first.lease_token is not None
    assert await complete_task(db_session, ordinary.id, first.lease_token, evaluation_cp=0) is True
    assert (await get_task(db_session, ordinary.id)).evaluation_cp == 0

    mate = await create_task(db_session, "fen two", {})
    await db_session.commit()
    second = await claim_one(db_session)
    assert second is not None and second.lease_token is not None
    assert await complete_task(
        db_session, mate.id, second.lease_token, mate_winner="black", mate_moves=3
    ) is True
    result = await get_task(db_session, mate.id)
    assert result is not None and result.mate_winner == "black" and result.mate_moves == 3


@pytest.mark.asyncio
async def test_result_shape_validation(db_session):
    task = await create_task(db_session, "valid fen", {})
    await db_session.commit()
    claimed = await claim_one(db_session)
    assert claimed is not None and claimed.lease_token is not None
    with pytest.raises(ValueError):
        await complete_task(db_session, task.id, claimed.lease_token)


@pytest.mark.asyncio
async def test_first_expired_lease_requeues_and_preserves_attempt_start(db_session):
    task = await create_task(db_session, "valid fen", {})
    await db_session.commit()
    first = await claim_one(db_session)
    assert first is not None and first.lease_token is not None
    started_at = first.started_at
    first.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    await db_session.commit()

    assert await recover_expired(db_session) == 1
    current = await get_task(db_session, task.id)
    assert current is not None
    assert current.status == TaskStatus.QUEUED.value
    assert current.attempts == 1
    assert current.started_at == started_at
    assert current.lease_token is None


@pytest.mark.asyncio
async def test_second_expired_lease_is_terminal_failure(db_session):
    task = await create_task(db_session, "valid fen", {})
    await db_session.commit()
    first = await claim_one(db_session)
    assert first is not None
    first.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    await db_session.commit()
    assert await recover_expired(db_session) == 1

    second = await claim_one(db_session)
    assert second is not None
    second.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    await db_session.commit()
    assert await recover_expired(db_session) == 1
    current = await get_task(db_session, task.id)
    assert current is not None
    assert current.status == TaskStatus.FAILED.value
    assert current.error_code == "LEASE_EXPIRED"
    assert current.finished_at is not None
    assert current.started_at is not None


@pytest.mark.asyncio
async def test_old_token_rejected_after_expiry_recovery_and_new_claim(db_session):
    task = await create_task(db_session, "valid fen", {})
    await db_session.commit()
    old = await claim_one(db_session)
    assert old is not None and old.lease_token is not None
    old_token = old.lease_token
    old.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    await db_session.commit()
    assert await recover_expired(db_session) == 1
    new = await claim_one(db_session)
    assert new is not None and new.lease_token is not None
    assert new.lease_token != old_token
    assert await complete_task(db_session, task.id, old_token, evaluation_cp=34) is False
    current = await get_task(db_session, task.id)
    assert current is not None and current.status == TaskStatus.RUNNING.value
