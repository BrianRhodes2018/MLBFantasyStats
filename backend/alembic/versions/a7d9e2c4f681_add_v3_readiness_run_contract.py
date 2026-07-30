"""add v3 readiness run contract

Revision ID: a7d9e2c4f681
Revises: f6c2b8d0e314
Create Date: 2026-07-30
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7d9e2c4f681"
down_revision: Union[str, Sequence[str], None] = "f6c2b8d0e314"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "hit_pick_runs",
        sa.Column("comparison_group_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "hit_pick_runs",
        sa.Column(
            "prediction_window",
            sa.String(length=20),
            nullable=False,
            server_default="legacy",
        ),
    )
    op.add_column(
        "hit_pick_runs",
        sa.Column(
            "model_role",
            sa.String(length=20),
            nullable=False,
            server_default="archive",
        ),
    )
    op.add_column(
        "hit_pick_runs",
        sa.Column(
            "is_visible",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "hit_pick_runs",
        sa.Column(
            "probability_status",
            sa.String(length=20),
            nullable=False,
            server_default="legacy_unknown",
        ),
    )

    # Preserve the old public board as the primary visible board. Other
    # historical snapshots remain available by UUID but are not newly exposed.
    op.execute(
        """
        update hit_pick_runs
        set model_role = case
                when is_public = 1 then 'primary'
                when lower(model_version) like '%v3%' then 'challenger'
                else 'archive'
            end,
            is_visible = case
                when is_public = 1 or lower(model_version) like '%v3%' then 1
                else 0
            end,
            probability_status = case
                when lower(model_version) like '%_cal' then 'calibrated'
                when lower(model_version) like '%v3%' then 'experimental'
                else 'legacy_unknown'
            end
        """
    )

    op.create_index(
        "ix_hit_pick_runs_comparison_group",
        "hit_pick_runs",
        ["comparison_group_id"],
        unique=False,
    )
    op.create_index(
        "uq_hit_pick_runs_primary_evaluation_window",
        "hit_pick_runs",
        ["pick_date", "prediction_window"],
        unique=True,
        postgresql_where=sa.text("model_role = 'primary' and is_evaluation = 1"),
        sqlite_where=sa.text("model_role = 'primary' and is_evaluation = 1"),
    )
    op.create_index(
        "uq_hit_pick_runs_model_evaluation_window",
        "hit_pick_runs",
        ["pick_date", "model_version", "prediction_window"],
        unique=True,
        postgresql_where=sa.text("is_evaluation = 1"),
        sqlite_where=sa.text("is_evaluation = 1"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_hit_pick_runs_model_evaluation_window",
        table_name="hit_pick_runs",
    )
    op.drop_index(
        "uq_hit_pick_runs_primary_evaluation_window",
        table_name="hit_pick_runs",
    )
    op.drop_index("ix_hit_pick_runs_comparison_group", table_name="hit_pick_runs")
    op.drop_column("hit_pick_runs", "probability_status")
    op.drop_column("hit_pick_runs", "is_visible")
    op.drop_column("hit_pick_runs", "model_role")
    op.drop_column("hit_pick_runs", "prediction_window")
    op.drop_column("hit_pick_runs", "comparison_group_id")
