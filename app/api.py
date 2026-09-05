"""FastAPI application exposing exactly the MVP submit and poll endpoints.

Persistence assumptions (the database implementation is owned separately):

* ``app.db.get_session`` is a FastAPI dependency yielding a synchronous or
  asynchronous database session.
* ``app.tasks.create_task(session, fen, evaluation_config, task_id=...)``
  inserts the already validated FEN and flushes it; this handler commits
  before returning and raises on database failure.
* ``app.tasks.get_task(session, task_id)`` returns a task row/model or ``None``
  and raises on database failure.

The small awaitable helper below permits either sync or async implementations
of those operations while keeping those names and argument order explicit.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse, Response

from .config import get_settings
from .facts.models import FactualResult
from .db import get_session
from .schemas import TaskCreate, TaskResponse, TaskStatus
from .tasks import create_task, get_task

REQUEST_LIMIT_BYTES = 4 * 1024

class RequestBodyLimitMiddleware:
    """Pure-ASGI bounded body reader.

    The request is consumed once, then replayed to the downstream app as one
    complete ``http.request`` event.  A subsequent receive returns disconnect,
    never another copy of the body.  This handles chunked requests and avoids
    depending on Starlette's private ``Request._receive`` implementation.
    """

    def __init__(self, application: Callable[..., Awaitable[None]], limit: int) -> None:
        self.application = application
        self.limit = limit

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Awaitable[dict[str, Any]]],
        send: Callable[..., Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self.application(scope, receive, send)
            return

        content_length = next(
            (
                value
                for key, value in scope.get("headers", [])
                if key.lower() == b"content-length"
            ),
            None,
        )
        try:
            oversized = content_length is not None and int(content_length) > self.limit
        except (TypeError, ValueError):
            oversized = False

        if oversized:
            await self._send_413(send)
            return

        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message.get("type") == "http.disconnect":
                # Preserve an early client disconnect for the downstream app.
                async def disconnected_receive(
                    disconnect_message: dict[str, Any] = message,
                ) -> dict[str, Any]:
                    return disconnect_message

                await self.application(scope, disconnected_receive, send)
                return
            if message.get("type") != "http.request":
                continue
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > self.limit:
                await self._send_413(send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break

        body = b"".join(chunks)
        consumed = False

        async def replay_once() -> dict[str, Any]:
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        async def send_with_policy(message: dict[str, Any]) -> None:
            path = scope.get("path", "")
            is_poll_path = (
                isinstance(path, str) and path.startswith("/tasks/")
            ) or (isinstance(path, bytes) and path.startswith(b"/tasks/"))
            if (
                message.get("type") == "http.response.start"
                and scope.get("method") == "GET"
                and is_poll_path
            ):
                headers = list(message.get("headers", []))
                headers = [
                    (key, value) for key, value in headers if key.lower() != b"cache-control"
                ]
                headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        await self.application(scope, replay_once, send_with_policy)

    async def _send_413(self, send: Callable[..., Awaitable[None]]) -> None:
        body = b'{"detail":"request body exceeds 4 KiB limit"}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


app.add_middleware(RequestBodyLimitMiddleware, limit=REQUEST_LIMIT_BYTES)


def _row_value(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(name, default)
    return getattr(row, name, default)


def _status(row: Any) -> str:
    value = _row_value(row, "status")
    return getattr(value, "value", str(value))


def _factual_result(row: Any) -> dict[str, Any] | None:
    value = _row_value(row, "factual_result")
    if value is None:
        return None
    bundle = FactualResult.model_validate(value)
    return {
        "facts": bundle.facts.model_dump(mode="json"),
        "explanation": bundle.explanation,
        "explanation_version": bundle.version,
    }


def _task_payload(row: Any) -> dict[str, Any]:
    task_id = _row_value(row, "id", _row_value(row, "task_id"))
    status = _status(row)
    payload: dict[str, Any] = {
        "task_id": str(task_id),
        "status": status,
        "evaluation": None,
        "facts": None,
        "explanation": None,
        "explanation_version": None,
    }

    if status == TaskStatus.COMPLETED.value:
        mate_winner = _row_value(row, "mate_winner")
        mate_moves = _row_value(row, "mate_moves")
        if mate_winner is not None or mate_moves is not None:
            payload["mate"] = {"winner": mate_winner, "moves": mate_moves}
        else:
            evaluation_cp = _row_value(row, "evaluation_cp")
            if evaluation_cp is not None:
                payload["evaluation"] = evaluation_cp / 100.0
    elif status == TaskStatus.FAILED.value:
        payload["error"] = {
            "code": _row_value(row, "error_code"),
            "message": _row_value(row, "error_message"),
        }

    factual_result = _factual_result(row)
    if factual_result is not None and status == TaskStatus.COMPLETED.value:
        payload.update(factual_result)
    return payload


@app.post("/tasks", status_code=202)
async def submit_task(payload: TaskCreate, session: Any = Depends(get_session)) -> Response:
    task_id = uuid4()
    try:
        # ``create_task`` flushes the row, while this handler owns the commit
        # boundary that makes the 202 acknowledgement durable.
        await _maybe_await(
            create_task(
                session,
                payload.fen,
                get_settings().evaluation_config(),
                task_id=task_id,
            )
        )
        await _maybe_await(session.commit())
    except Exception:
        # A failed insert/commit must never be acknowledged as a task.
        try:
            await _maybe_await(session.rollback())
        except Exception:
            # The original database error is the useful client-facing result;
            # rollback is best effort when the connection is already gone.
            pass
        return JSONResponse(status_code=503, content={"detail": "database unavailable"})
    return JSONResponse(
        status_code=202,
        headers={"Location": f"/tasks/{task_id}"},
        content={"task_id": str(task_id)},
    )


@app.get("/tasks/{task_id}")
async def poll_task(task_id: UUID, session: Any = Depends(get_session)) -> Response:
    try:
        row = await _maybe_await(get_task(session, task_id))
    except Exception:
        return JSONResponse(status_code=503, content={"detail": "database unavailable"})
    if row is None:
        return JSONResponse(status_code=404, content={"detail": "task not found"})
    try:
        payload = TaskResponse.model_validate(_task_payload(row))
        return JSONResponse(content=payload.model_dump(mode="json"))
    except Exception:
        return JSONResponse(status_code=503, content={"detail": "task result unavailable"})


__all__ = ["REQUEST_LIMIT_BYTES", "app", "poll_task", "submit_task"]
