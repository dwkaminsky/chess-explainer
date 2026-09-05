"""Add factual task result bundle."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_add_factual_result"
down_revision = "0001_create_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "factual_result",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_tasks_factual_result_completed_only",
        "tasks",
        "status = 'completed' OR factual_result IS NULL",
    )
    op.create_check_constraint(
        "ck_tasks_factual_result_json_object",
        "tasks",
        "factual_result IS NULL OR jsonb_typeof(factual_result) = 'object'",
    )


def downgrade() -> None:
    op.drop_constraint("ck_tasks_factual_result_json_object", "tasks", type_="check")
    op.drop_constraint("ck_tasks_factual_result_completed_only", "tasks", type_="check")
    op.drop_column("tasks", "factual_result")
