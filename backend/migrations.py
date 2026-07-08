"""
migrations.py - Alembic Migration Bootstrap
============================================

Brings the database schema up to date at startup using Alembic's
versioned migrations (backend/alembic/versions/). This replaced the old
hand-rolled "add missing columns" helper: every schema change is now a
numbered, reviewable, reversible migration file — git history for the
database.

Why this lives in its own module:
    Both `main.py` (FastAPI startup) and `daily_update.py` (GitHub
    Actions cron) need the schema current before touching data.

How the three database states are handled:
    1. Fresh, empty database        -> `upgrade head` runs every
       migration from the baseline, creating the full schema.
    2. Pre-Alembic database         -> it already has the tables the
       baseline would create (built over time by the old create_all()
       flow) but no alembic_version bookkeeping table. It gets STAMPED:
       marked as already at the baseline, applying nothing.
    3. Alembic-managed database     -> `upgrade head` applies only the
       migrations it hasn't seen. The common case going forward.

Day-to-day workflow for a schema change:
    1. Edit models.py
    2. cd backend && alembic revision --autogenerate -m "what changed"
    3. Review the generated file in alembic/versions/, commit it
    4. Every environment upgrades itself on next startup/deploy

Calling `run_migrations()` remains idempotent and safe on every startup.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from database import engine

BACKEND_DIR = Path(__file__).resolve().parent

# Any table from the pre-Alembic era works as the "this database already
# has a schema" marker; players is the app's oldest table.
_PRE_ALEMBIC_MARKER_TABLE = "players"


def _alembic_config() -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    # Absolute path so this works no matter the process's working
    # directory (uvicorn on Render, pytest, daily_update on a runner).
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return config


def run_migrations():
    """Bring the connected database to the current schema revision."""
    config = _alembic_config()
    inspector = inspect(engine)
    has_version_table = inspector.has_table("alembic_version")
    has_legacy_schema = inspector.has_table(_PRE_ALEMBIC_MARKER_TABLE)

    if not has_version_table and has_legacy_schema:
        # Pre-Alembic database: schema already matches the baseline
        # (built over time by create_all + the old column adder).
        # Record that fact without executing any DDL.
        command.stamp(config, "head")
        print("migrations: pre-Alembic database stamped at baseline")
    else:
        command.upgrade(config, "head")
