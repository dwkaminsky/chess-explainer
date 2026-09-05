"""SQLAlchemy mappings and database-level task invariants."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON, Uuid


class Base(DeclarativeBase):
    pass


class TaskStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True).with_variant(PGUUID(as_uuid=True), "postgresql"),
        primary_key=True,
        default=uuid.uuid4,
    )
    fen: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=TaskStatus.QUEUED.value)
    evaluation_cp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mate_winner: Mapped[str | None] = mapped_column(String(5), nullable=True)
    mate_moves: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    lease_token: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True).with_variant(PGUUID(as_uuid=True), "postgresql"), nullable=True
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    engine_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    factual_result: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )
    evaluation_config: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')", name="ck_tasks_status"
        ),
        CheckConstraint("attempts >= 0 AND attempts <= 2", name="ck_tasks_attempts"),
        CheckConstraint(
            "(mate_winner IS NULL AND mate_moves IS NULL) OR "
            "(mate_winner IS NOT NULL AND mate_winner IN ('white', 'black') "
            "AND mate_moves IS NOT NULL AND mate_moves >= 0)",
            name="ck_tasks_mate_result",
        ),
        CheckConstraint(
            "NOT (evaluation_cp IS NOT NULL AND (mate_winner IS NOT NULL OR mate_moves IS NOT NULL))",
            name="ck_tasks_result_exclusive",
        ),
        CheckConstraint(
            "(status = 'running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'running' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_tasks_running_lease",
        ),
        CheckConstraint(
            "status NOT IN ('queued', 'running', 'failed') OR "
            "(evaluation_cp IS NULL AND mate_winner IS NULL AND mate_moves IS NULL)",
            name="ck_tasks_nonterminal_no_result",
        ),
        CheckConstraint(
            "status <> 'failed' OR (error_code IS NOT NULL AND error_message IS NOT NULL)",
            name="ck_tasks_failed_error",
        ),
        CheckConstraint(
            "status IN ('completed', 'failed') OR finished_at IS NULL",
            name="ck_tasks_nonterminal_finished",
        ),
        CheckConstraint(
            "status <> 'completed' OR evaluation_cp IS NOT NULL OR mate_winner IS NOT NULL",
            name="ck_tasks_completed_has_result",
        ),
        CheckConstraint(
            "status NOT IN ('completed', 'failed') OR "
            "(finished_at IS NOT NULL AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_tasks_terminal_fields",
        ),
        CheckConstraint(
            "status = 'completed' OR factual_result IS NULL",
            name="ck_tasks_factual_result_completed_only",
        ),
        # PostgreSQL uses these predicates to keep the queue indexes small.  The
        # predicates are portable enough for SQLite's test schema as well.
        Index(
            "ix_tasks_queued_order",
            "created_at",
            "id",
            postgresql_where=text("status = 'queued'"),
        ),
        Index(
            "ix_tasks_running_lease",
            "lease_expires_at",
            postgresql_where=text("status = 'running'"),
        ),
    )
