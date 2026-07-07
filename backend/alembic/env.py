"""
Alembic environment — wires migrations to this app's own configuration.

Instead of putting a connection string in alembic.ini (it would be a
committed secret), the database URL comes from the same place the app
gets it: backend/.env via database.py. Override with ALEMBIC_DATABASE_URL
when you need to run migrations against a different database (e.g.
autogenerating a migration by diffing against a scratch database).

target_metadata is the app's shared MetaData registry — importing
`models` registers every table on it, which is what powers
`alembic revision --autogenerate`.
"""

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

# Make backend/ importable regardless of where alembic is invoked from.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from database import SYNC_URL, metadata, normalize_database_url  # noqa: E402
import models  # noqa: F401,E402  (imported for its side effect: table registration)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata


def _database_url() -> str:
    override = os.environ.get("ALEMBIC_DATABASE_URL")
    if override:
        _, sync_url = normalize_database_url(override)
        return sync_url
    return SYNC_URL


def run_migrations_offline() -> None:
    """Emit migration SQL to stdout without a live connection."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against the live database."""
    connectable = create_engine(_database_url(), poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
