"""Add candidate task result bundle."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_add_candidate_result"
down_revision = "0002_add_factual_result"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "candidate_result",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_tasks_candidate_result_completed_only",
        "tasks",
        "status = 'completed' OR candidate_result IS NULL",
    )
    op.create_check_constraint(
        "ck_tasks_candidate_result_json_object",
        "tasks",
        "candidate_result IS NULL OR jsonb_typeof(candidate_result) = 'object'",
    )


def downgrade() -> None:
    op.drop_constraint("ck_tasks_candidate_result_json_object", "tasks", type_="check")
    op.drop_constraint("ck_tasks_candidate_result_completed_only", "tasks", type_="check")
    op.drop_column("tasks", "candidate_result")
