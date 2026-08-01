"""add pitcher k projection runs

Revision ID: b3f1d6a9c420
Revises: a7d9e2c4f681
Create Date: 2026-08-01
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b3f1d6a9c420"
down_revision: Union[str, Sequence[str], None] = "a7d9e2c4f681"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("pitcher_k_runs"):
        op.create_table(
        "pitcher_k_runs",
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("projection_date", sa.String(length=10), nullable=False),
        sa.Column("approach", sa.String(length=30), nullable=False),
        sa.Column("model_version", sa.String(length=50), nullable=False),
        sa.Column("generated_at", sa.String(length=40), nullable=False),
        sa.Column("as_of_timestamp", sa.String(length=40), nullable=False),
        sa.Column("prediction_window", sa.String(length=20), nullable=False),
        sa.Column("comparison_group_id", sa.String(length=36), nullable=False),
        sa.Column("candidate_cohort_id", sa.String(length=64), nullable=False),
        sa.Column("trained_through", sa.String(length=10), nullable=False),
        sa.Column("trained_on_rows", sa.Integer(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("backtest_metrics_json", sa.Text(), nullable=False),
        sa.Column("model_manifest_json", sa.Text(), nullable=False),
        sa.Column("is_public", sa.Integer(), server_default="1", nullable=False),
        sa.Column("is_evaluation", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("run_id"),
        )
        op.create_index(
            "ix_pitcher_k_runs_date_approach_public",
            "pitcher_k_runs",
            ["projection_date", "approach", "is_public"],
            unique=False,
        )
        op.create_index(
            "ix_pitcher_k_runs_comparison_group",
            "pitcher_k_runs",
            ["comparison_group_id"],
            unique=False,
        )
    if not inspector.has_table("pitcher_k_predictions"):
        op.create_table(
        "pitcher_k_predictions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("projection_date", sa.String(length=10), nullable=False),
        sa.Column("approach", sa.String(length=30), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("game_pk", sa.Integer(), nullable=False),
        sa.Column("game_time", sa.String(length=40), nullable=True),
        sa.Column("pitcher_id", sa.Integer(), nullable=False),
        sa.Column("pitcher_name", sa.String(length=150), nullable=False),
        sa.Column("team", sa.String(length=80), nullable=False),
        sa.Column("opponent", sa.String(length=80), nullable=False),
        sa.Column("venue", sa.String(length=120), nullable=True),
        sa.Column("pitcher_throws", sa.String(length=2), nullable=True),
        sa.Column("lineup_source", sa.String(length=40), nullable=False),
        sa.Column("lineup_confidence", sa.Float(), nullable=False),
        sa.Column("projected_ks", sa.Float(), nullable=False),
        sa.Column("median_ks", sa.Integer(), nullable=False),
        sa.Column("p10_ks", sa.Integer(), nullable=False),
        sa.Column("p90_ks", sa.Integer(), nullable=False),
        sa.Column("probability_5_plus", sa.Float(), nullable=False),
        sa.Column("probability_6_plus", sa.Float(), nullable=False),
        sa.Column("projected_batters_faced", sa.Float(), nullable=False),
        sa.Column("pmf_json", sa.Text(), nullable=False),
        sa.Column("actual_ks", sa.Integer(), nullable=True),
        sa.Column("actual_batters_faced", sa.Integer(), nullable=True),
        sa.Column("graded_at", sa.String(length=40), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["pitcher_k_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "game_pk",
            "pitcher_id",
            name="uq_pitcher_k_prediction_run_game_pitcher",
        ),
        )
        op.create_index(
            "ix_pitcher_k_predictions_date_approach",
            "pitcher_k_predictions",
            ["projection_date", "approach"],
            unique=False,
        )
        op.create_index(
            "ix_pitcher_k_predictions_game_pitcher",
            "pitcher_k_predictions",
            ["game_pk", "pitcher_id"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index(
        "ix_pitcher_k_predictions_game_pitcher",
        table_name="pitcher_k_predictions",
    )
    op.drop_index(
        "ix_pitcher_k_predictions_date_approach",
        table_name="pitcher_k_predictions",
    )
    op.drop_table("pitcher_k_predictions")
    op.drop_index(
        "ix_pitcher_k_runs_comparison_group",
        table_name="pitcher_k_runs",
    )
    op.drop_index(
        "ix_pitcher_k_runs_date_approach_public",
        table_name="pitcher_k_runs",
    )
    op.drop_table("pitcher_k_runs")
