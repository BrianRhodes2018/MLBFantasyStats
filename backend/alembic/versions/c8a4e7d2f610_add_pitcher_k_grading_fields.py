"""add pitcher k grading fields

Revision ID: c8a4e7d2f610
Revises: b3f1d6a9c420
Create Date: 2026-08-02
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8a4e7d2f610"
down_revision: Union[str, Sequence[str], None] = "b3f1d6a9c420"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_COLUMNS = (
    sa.Column("actual_innings_pitched", sa.Float(), nullable=True),
    sa.Column("actual_pitch_count", sa.Integer(), nullable=True),
    sa.Column("result_status", sa.String(length=30), nullable=True),
    sa.Column("started", sa.Integer(), nullable=True),
    sa.Column("game_status", sa.String(length=60), nullable=True),
    sa.Column("grading_source", sa.String(length=80), nullable=True),
    sa.Column("grade_detail", sa.String(length=200), nullable=True),
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {
        column["name"]
        for column in inspector.get_columns("pitcher_k_predictions")
    }
    for column in _COLUMNS:
        if column.name not in existing:
            op.add_column("pitcher_k_predictions", column)

    inspector = sa.inspect(op.get_bind())
    indexes = {
        index["name"]
        for index in inspector.get_indexes("pitcher_k_predictions")
    }
    if "ix_pitcher_k_predictions_date_status" not in indexes:
        op.create_index(
            "ix_pitcher_k_predictions_date_status",
            "pitcher_k_predictions",
            ["projection_date", "result_status"],
            unique=False,
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    indexes = {
        index["name"]
        for index in inspector.get_indexes("pitcher_k_predictions")
    }
    if "ix_pitcher_k_predictions_date_status" in indexes:
        op.drop_index(
            "ix_pitcher_k_predictions_date_status",
            table_name="pitcher_k_predictions",
        )

    inspector = sa.inspect(op.get_bind())
    existing = {
        column["name"]
        for column in inspector.get_columns("pitcher_k_predictions")
    }
    for column in reversed(_COLUMNS):
        if column.name in existing:
            op.drop_column("pitcher_k_predictions", column.name)
