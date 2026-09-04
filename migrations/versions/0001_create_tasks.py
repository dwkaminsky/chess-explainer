"""Create durable task queue.

Revision ID: 0001_create_tasks
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_create_tasks"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fen", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("evaluation_cp", sa.Integer(), nullable=True),
        sa.Column("mate_winner", sa.String(length=5), nullable=True),
        sa.Column("mate_moves", sa.Integer(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("engine_version", sa.String(length=128), nullable=True),
        sa.Column("evaluation_config", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('queued', 'running', 'completed', 'failed')", name="ck_tasks_status"),
        sa.CheckConstraint("attempts >= 0 AND attempts <= 2", name="ck_tasks_attempts"),
        sa.CheckConstraint(
            "(mate_winner IS NULL AND mate_moves IS NULL) OR "
            "(mate_winner IS NOT NULL AND mate_winner IN ('white', 'black') "
            "AND mate_moves IS NOT NULL AND mate_moves >= 0)",
            name="ck_tasks_mate_result",
        ),
        sa.CheckConstraint(
            "NOT (evaluation_cp IS NOT NULL AND (mate_winner IS NOT NULL OR mate_moves IS NOT NULL))",
            name="ck_tasks_result_exclusive",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'running' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_tasks_running_lease",
        ),
        sa.CheckConstraint(
            "status NOT IN ('queued', 'running', 'failed') OR "
            "(evaluation_cp IS NULL AND mate_winner IS NULL AND mate_moves IS NULL)",
            name="ck_tasks_nonterminal_no_result",
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR (error_code IS NOT NULL AND error_message IS NOT NULL)",
            name="ck_tasks_failed_error",
        ),
        sa.CheckConstraint(
            "status IN ('completed', 'failed') OR finished_at IS NULL",
            name="ck_tasks_nonterminal_finished",
        ),
        sa.CheckConstraint(
            "status <> 'completed' OR evaluation_cp IS NOT NULL OR mate_winner IS NOT NULL",
            name="ck_tasks_completed_has_result",
        ),
        sa.CheckConstraint(
            "status NOT IN ('completed', 'failed') OR "
            "(finished_at IS NOT NULL AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_tasks_terminal_fields",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tasks_queued_order", "tasks", ["created_at", "id"], unique=False,
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "ix_tasks_running_lease", "tasks", ["lease_expires_at"], unique=False,
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_running_lease", table_name="tasks")
    op.drop_index("ix_tasks_queued_order", table_name="tasks")
    op.drop_table("tasks")
