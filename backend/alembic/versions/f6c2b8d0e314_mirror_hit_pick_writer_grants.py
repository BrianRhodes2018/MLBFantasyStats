"""mirror hit pick writer grants

Revision ID: f6c2b8d0e314
Revises: e5b1a7c9d203
Create Date: 2026-07-29
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6c2b8d0e314"
down_revision: Union[str, Sequence[str], None] = "e5b1a7c9d203"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_MIRROR_GRANTS_SQL = """
DO $$
DECLARE
    grant_row record;
BEGIN
    FOR grant_row IN
        SELECT
            grantee,
            string_agg(privilege_type, ', ' ORDER BY privilege_type) AS privileges
        FROM information_schema.table_privileges
        WHERE table_schema = current_schema()
          AND table_name = 'hit_picks'
          AND privilege_type IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE')
          AND grantee <> current_user
          AND grantee <> 'PUBLIC'
        GROUP BY grantee
    LOOP
        EXECUTE format(
            'GRANT %s ON TABLE hit_pick_runs TO %I',
            grant_row.privileges,
            grant_row.grantee
        );
    END LOOP;
END
$$;
"""


_REVOKE_MIRRORED_GRANTS_SQL = """
DO $$
DECLARE
    grant_row record;
BEGIN
    FOR grant_row IN
        SELECT DISTINCT grantee
        FROM information_schema.table_privileges
        WHERE table_schema = current_schema()
          AND table_name = 'hit_picks'
          AND privilege_type IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE')
          AND grantee <> current_user
          AND grantee <> 'PUBLIC'
    LOOP
        EXECUTE format(
            'REVOKE SELECT, INSERT, UPDATE, DELETE '
            'ON TABLE hit_pick_runs FROM %I',
            grant_row.grantee
        );
    END LOOP;
END
$$;
"""


def upgrade() -> None:
    # The scheduled prediction job may use a least-privilege writer account
    # rather than the schema owner used by Render migrations. Mirror each
    # non-owner role's existing DML grants from hit_picks onto the new parent
    # run table without hard-coding an environment-specific role name.
    op.execute(sa.text(_MIRROR_GRANTS_SQL))


def downgrade() -> None:
    op.execute(sa.text(_REVOKE_MIRRORED_GRANTS_SQL))
