"""Durable task-queue operations.

The task row is the queue.  Claiming and recovery use short database
transactions; no transaction is held while an engine process is running.
Mutations that belong to a worker attempt are guarded by both its UUID lease
token and the database's current time, so a delayed worker cannot overwrite a
newer attempt.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import Select, and_, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Task, TaskStatus

MAX_ATTEMPTS = 2


def _is_postgres(session: AsyncSession) -> bool:
    return session.bind is not None and session.bind.dialect.name == "postgresql"


def _db_now(session: AsyncSession):
    # CURRENT_TIMESTAMP is transaction-scoped in PostgreSQL.  A wall-clock
    # database function avoids a stale lease being accepted when a completion
    # transaction happens to straddle the expiry instant.
    return func.clock_timestamp() if _is_postgres(session) else func.current_timestamp()


async def create_task(
    session: AsyncSession,
    fen: str,
    evaluation_config: dict[str, Any],
    *,
    task_id: uuid.UUID | None = None,
) -> Task:
    """Insert a queued task and flush it.

    The caller owns the surrounding transaction and must commit before
    acknowledging an HTTP submission.  Keeping that responsibility explicit
    makes a failed commit impossible to confuse with a durable task.
    """

    task = Task(
        id=task_id or uuid.uuid4(),
        fen=fen,
        status=TaskStatus.QUEUED.value,
        attempts=0,
        evaluation_config=dict(evaluation_config),
    )
    session.add(task)
    await session.flush()
    return task


async def get_task(session: AsyncSession, task_id: uuid.UUID) -> Task | None:
    return await session.scalar(select(Task).where(Task.id == task_id))


async def claim_one(
    session: AsyncSession,
    *,
    lease_seconds: float = 30.0,
) -> Task | None:
    """Claim the oldest available task and commit the claim.

    PostgreSQL's ``FOR UPDATE SKIP LOCKED`` allows independent workers to
    compete without waiting on one another.  SQLite (used by unit tests) does
    not implement row locks; its dialect fallback retains the same state
    transition but is not intended for multi-worker production use.
    """

    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    # A claim must be a standalone short transaction.  A fresh session is the
    # normal path; committing a prior read transaction avoids accidentally
    # retaining a lock when callers reuse a session.
    if session.in_transaction():
        await session.commit()
    async with session.begin():
        query: Select[tuple[Task]] = (
            select(Task)
            .where(Task.status == TaskStatus.QUEUED.value)
            .order_by(Task.created_at.asc(), Task.id.asc())
            .limit(1)
        )
        if _is_postgres(session):
            query = query.with_for_update(skip_locked=True)
        task = await session.scalar(query)
        if task is None:
            return None
        token = uuid.uuid4()
        task.status = TaskStatus.RUNNING.value
        task.attempts += 1
        task.lease_token = token
        task.started_at = func.current_timestamp()  # type: ignore[assignment]
        if _is_postgres(session):
            # Keep the timestamp calculation inside PostgreSQL; application
            # clock skew must not make a lease appear valid or expired.
            task.lease_expires_at = text(
                "CURRENT_TIMESTAMP + (:lease_seconds * INTERVAL '1 second')"
            ).bindparams(lease_seconds=lease_seconds)  # type: ignore[assignment]
        else:
            task.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
        task.evaluation_cp = None
        task.mate_winner = None
        task.mate_moves = None
        task.error_code = None
        task.error_message = None
        task.engine_version = None
        task.finished_at = None
        await session.flush()
        await session.refresh(task)
        return task


async def recover_expired(
    session: AsyncSession,
    *,
    max_attempts: int = MAX_ATTEMPTS,
    error_code: str = "LEASE_EXPIRED",
    error_message: str = "The worker lease expired before evaluation finished.",
) -> int:
    """Recover all currently expired running tasks and return transition count."""

    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    if session.in_transaction():
        await session.commit()
    count = 0
    async with session.begin():
        query = select(Task).where(
            Task.status == TaskStatus.RUNNING.value,
            Task.lease_expires_at.is_not(None),
            Task.lease_expires_at <= _db_now(session),
        )
        if _is_postgres(session):
            query = query.with_for_update(skip_locked=True)
        rows = (await session.scalars(query)).all()
        for task in rows:
            count += 1
            if task.attempts < max_attempts:
                _clear_attempt(task)
                task.status = TaskStatus.QUEUED.value
            else:
                _clear_attempt(task)
                task.status = TaskStatus.FAILED.value
                task.error_code = error_code
                task.error_message = error_message
                task.finished_at = func.current_timestamp()  # type: ignore[assignment]
        await session.flush()
    return count


def _clear_attempt(task: Task) -> None:
    task.lease_token = None
    task.lease_expires_at = None
    task.evaluation_cp = None
    task.mate_winner = None
    task.mate_moves = None
    task.engine_version = None
    task.finished_at = None
    task.error_code = None
    task.error_message = None


def _attempt_guard(session: AsyncSession, task_id: uuid.UUID, lease_token: uuid.UUID):
    return and_(
        Task.id == task_id,
        Task.status == TaskStatus.RUNNING.value,
        Task.lease_token == lease_token,
        Task.lease_expires_at > _db_now(session),
    )


async def complete_task(
    session: AsyncSession,
    task_id: uuid.UUID,
    lease_token: uuid.UUID,
    *,
    evaluation_cp: int | None = None,
    mate_winner: Literal["white", "black"] | None = None,
    mate_moves: int | None = None,
    engine_version: str | None = None,
) -> bool:
    """Persist a successful attempt if its token and lease are still valid."""

    ordinary = evaluation_cp is not None
    mate = mate_winner is not None or mate_moves is not None
    if ordinary == mate:
        raise ValueError("provide exactly one complete ordinary or mate result")
    if mate and (mate_winner not in ("white", "black") or mate_moves is None or mate_moves < 0):
        raise ValueError("mate result requires winner and nonnegative moves")
    if session.in_transaction():
        await session.commit()
    async with session.begin():
        result = await session.execute(
            update(Task)
            .where(_attempt_guard(session, task_id, lease_token))
            .values(
                status=TaskStatus.COMPLETED.value,
                evaluation_cp=evaluation_cp,
                mate_winner=mate_winner,
                mate_moves=mate_moves,
                engine_version=engine_version,
                error_code=None,
                error_message=None,
                lease_token=None,
                lease_expires_at=None,
                finished_at=func.current_timestamp(),
            )
        )
        return result.rowcount == 1


async def fail_or_retry(
    session: AsyncSession,
    task_id: uuid.UUID,
    lease_token: uuid.UUID,
    *,
    error_code: str,
    error_message: str,
    max_attempts: int = MAX_ATTEMPTS,
) -> Literal["requeued", "failed", "stale"]:
    """Retry a failed attempt once, or publish a client-visible failure.

    A stale token/expired lease returns ``stale`` and does not alter the row.
    """

    if not error_code or not error_message:
        raise ValueError("error_code and error_message are required")
    if session.in_transaction():
        await session.commit()
    async with session.begin():
        task = await session.scalar(
            select(Task).where(_attempt_guard(session, task_id, lease_token)).with_for_update()
        )
        if task is None:
            return "stale"
        if task.attempts < max_attempts:
            _clear_attempt(task)
            task.status = TaskStatus.QUEUED.value
            outcome: Literal["requeued", "failed"] = "requeued"
        else:
            _clear_attempt(task)
            task.status = TaskStatus.FAILED.value
            task.error_code = error_code[:64]
            task.error_message = error_message[:1000]
            task.finished_at = func.current_timestamp()  # type: ignore[assignment]
            outcome = "failed"
        await session.flush()
        return outcome


# Explicit aliases make the worker integration readable and preserve a small
# compatibility surface for callers that use plan terminology.
recover_leases = recover_expired
recover_expired_tasks = recover_expired
claim_next_task = claim_one
persist_failure = fail_or_retry
retry_task = fail_or_retry
# The worker chooses fail_task after the second attempt.  The same guarded
# implementation is safe for that path and still returns ``failed``.
fail_task = fail_or_retry
save_result = complete_task
