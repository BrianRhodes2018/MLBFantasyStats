"""add immutable hit pick runs

Revision ID: e5b1a7c9d203
Revises: d14b7c9a2e31
Create Date: 2026-07-29
"""

from __future__ import annotations

from typing import Sequence, Union
from uuid import NAMESPACE_URL, uuid5

from alembic import op
import sqlalchemy as sa


revision: str = "e5b1a7c9d203"
down_revision: Union[str, Sequence[str], None] = "d14b7c9a2e31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _legacy_run_id(pick_date: str, model_version: str) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            f"mlb-fantasy-stats/hit-picks/{pick_date}/{model_version}",
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    # The repository's squashable baseline also declares every current model
    # table for brand-new databases. Existing deployed databases reach this
    # revision without the table, so creation must support both paths.
    if not sa.inspect(bind).has_table("hit_pick_runs"):
        op.create_table(
            "hit_pick_runs",
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("pick_date", sa.String(length=10), nullable=False),
            sa.Column("model_version", sa.String(length=40), nullable=False),
            sa.Column("generated_at", sa.String(length=40), nullable=False),
            sa.Column("as_of_timestamp", sa.String(length=40), nullable=True),
            sa.Column("prediction_mode", sa.String(length=20), nullable=False),
            sa.Column("is_public", sa.Integer(), server_default="0", nullable=False),
            sa.Column("is_evaluation", sa.Integer(), server_default="1", nullable=False),
            sa.Column("trained_on_rows", sa.Integer(), nullable=True),
            sa.Column("candidate_cohort_id", sa.String(length=64), nullable=True),
            sa.Column("candidate_count", sa.Integer(), nullable=False),
            sa.Column("candidate_manifest_json", sa.Text(), nullable=True),
            sa.Column("runtime_manifest_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.String(length=40), nullable=False),
            sa.PrimaryKeyConstraint("run_id"),
        )
    run_indexes = {
        index["name"] for index in sa.inspect(bind).get_indexes("hit_pick_runs")
    }
    if "ix_hit_pick_runs_date_public" not in run_indexes:
        op.create_index(
            "ix_hit_pick_runs_date_public",
            "hit_pick_runs",
            ["pick_date", "is_public"],
            unique=False,
        )
    if "ix_hit_pick_runs_date_model_evaluation" not in run_indexes:
        op.create_index(
            "ix_hit_pick_runs_date_model_evaluation",
            "hit_pick_runs",
            ["pick_date", "model_version", "is_evaluation"],
            unique=False,
        )

    op.add_column("hit_picks", sa.Column("run_id", sa.String(length=36), nullable=True))
    op.add_column("hit_picks", sa.Column("game_pk", sa.Integer(), nullable=True))

    # Preserve the existing calendar/history rows by wrapping each legacy
    # date/model list in one deterministic run before run_id becomes required.
    legacy_groups = bind.execute(
        sa.text(
            """
            select pick_date, model_version,
                   max(generated_at) as generated_at,
                   max(trained_on_rows) as trained_on_rows,
                   max(is_public) as is_public,
                   count(*) as candidate_count
            from hit_picks
            group by pick_date, model_version
            """
        )
    ).mappings()
    for group in legacy_groups:
        run_id = _legacy_run_id(group["pick_date"], group["model_version"])
        generated_at = group["generated_at"] or f"{group['pick_date']}T00:00:00+00:00"
        bind.execute(
            sa.text(
                """
                insert into hit_pick_runs (
                    run_id, pick_date, model_version, generated_at,
                    as_of_timestamp, prediction_mode, is_public, is_evaluation,
                    trained_on_rows, candidate_cohort_id, candidate_count,
                    candidate_manifest_json, runtime_manifest_json, created_at
                ) values (
                    :run_id, :pick_date, :model_version, :generated_at,
                    null, 'legacy_unknown', :is_public, 1,
                    :trained_on_rows, null, :candidate_count,
                    null, null, :generated_at
                )
                """
            ),
            {
                "run_id": run_id,
                "pick_date": group["pick_date"],
                "model_version": group["model_version"],
                "generated_at": generated_at,
                "is_public": group["is_public"],
                "trained_on_rows": group["trained_on_rows"],
                "candidate_count": group["candidate_count"],
            },
        )
        bind.execute(
            sa.text(
                """
                update hit_picks
                set run_id = :run_id
                where pick_date = :pick_date and model_version = :model_version
                """
            ),
            {
                "run_id": run_id,
                "pick_date": group["pick_date"],
                "model_version": group["model_version"],
            },
        )

    op.alter_column("hit_picks", "run_id", existing_type=sa.String(length=36), nullable=False)
    op.create_foreign_key(
        "fk_hit_picks_run_id",
        "hit_picks",
        "hit_pick_runs",
        ["run_id"],
        ["run_id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "uq_hit_picks_date_model_rank",
        "hit_picks",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_hit_picks_run_rank",
        "hit_picks",
        ["run_id", "rank"],
    )
    op.create_index(
        "ix_hit_picks_game_player",
        "hit_picks",
        ["game_pk", "player_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_hit_picks_game_player", table_name="hit_picks")
    op.drop_constraint("uq_hit_picks_run_rank", "hit_picks", type_="unique")
    op.create_unique_constraint(
        "uq_hit_picks_date_model_rank",
        "hit_picks",
        ["pick_date", "model_version", "rank"],
    )
    op.drop_constraint("fk_hit_picks_run_id", "hit_picks", type_="foreignkey")
    op.drop_column("hit_picks", "game_pk")
    op.drop_column("hit_picks", "run_id")
    op.drop_index(
        "ix_hit_pick_runs_date_model_evaluation",
        table_name="hit_pick_runs",
    )
    op.drop_index("ix_hit_pick_runs_date_public", table_name="hit_pick_runs")
    # Keep the run records recoverable. The squashable baseline declares this
    # table for fresh databases, and re-upgrading will reuse it.
