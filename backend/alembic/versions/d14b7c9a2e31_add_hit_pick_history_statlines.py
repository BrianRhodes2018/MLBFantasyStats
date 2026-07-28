"""add hit pick history statlines

Revision ID: d14b7c9a2e31
Revises: c8a75a27355e
Create Date: 2026-07-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d14b7c9a2e31"
down_revision: Union[str, Sequence[str], None] = "c8a75a27355e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "hit_picks",
        sa.Column("is_public", sa.Integer(), nullable=False, server_default="1"),
    )
    for column_name in (
        "at_bats",
        "plate_appearances",
        "doubles",
        "triples",
        "home_runs",
        "runs",
        "rbi",
        "walks",
        "strikeouts",
        "total_bases",
    ):
        op.add_column("hit_picks", sa.Column(column_name, sa.Integer(), nullable=True))

    op.create_unique_constraint(
        "uq_hit_picks_date_model_rank",
        "hit_picks",
        ["pick_date", "model_version", "rank"],
    )
    op.create_index(
        "ix_hit_picks_date_public",
        "hit_picks",
        ["pick_date", "is_public"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_hit_picks_date_public", table_name="hit_picks")
    op.drop_constraint(
        "uq_hit_picks_date_model_rank",
        "hit_picks",
        type_="unique",
    )
    for column_name in reversed(
        (
            "at_bats",
            "plate_appearances",
            "doubles",
            "triples",
            "home_runs",
            "runs",
            "rbi",
            "walks",
            "strikeouts",
            "total_bases",
        )
    ):
        op.drop_column("hit_picks", column_name)
    op.drop_column("hit_picks", "is_public")
