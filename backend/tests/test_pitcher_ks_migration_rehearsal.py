from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, make_url

from database import SYNC_URL


BACKEND_DIR = Path(__file__).resolve().parents[1]
PRE_PITCHER_KS_REVISION = "a7d9e2c4f681"
PITCHER_KS_REVISION = "b3f1d6a9c420"
PITCHER_TABLES = {"pitcher_k_runs", "pitcher_k_predictions"}


def _config(database_url: URL, monkeypatch) -> Config:
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", database_url.render_as_string(False))
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return config


@pytest.fixture
def rehearsal_database(monkeypatch):
    configured_url = os.environ.get("PITCHER_KS_MIGRATION_TEST_URL")
    source_url = make_url(configured_url or SYNC_URL)
    if source_url.get_backend_name() != "postgresql":
        pytest.skip("Pitcher Ks migration rehearsal requires PostgreSQL.")
    if not configured_url and os.environ.get("CI"):
        pytest.skip("The dedicated PostgreSQL migration job runs this rehearsal.")
    if not configured_url and source_url.host not in {"localhost", "127.0.0.1"}:
        pytest.skip("Refusing to create a rehearsal database on a remote server.")

    database_name = f"pitcher_ks_rehearsal_{uuid4().hex}"
    admin_url = source_url.set(database="postgres")
    target_url = source_url.set(database=database_name)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")

    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    try:
        yield target_url
    finally:
        with admin_engine.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        admin_engine.dispose()
        monkeypatch.delenv("ALEMBIC_DATABASE_URL", raising=False)


def test_fresh_database_contains_pitcher_k_tables(
    rehearsal_database,
    monkeypatch,
):
    command.upgrade(_config(rehearsal_database, monkeypatch), "head")
    engine = create_engine(rehearsal_database)
    try:
        assert PITCHER_TABLES <= set(inspect(engine).get_table_names())
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        assert revision == PITCHER_KS_REVISION
    finally:
        engine.dispose()


def test_evolved_database_adds_tables_without_touching_existing_data(
    rehearsal_database,
    monkeypatch,
):
    config = _config(rehearsal_database, monkeypatch)
    command.upgrade(config, PRE_PITCHER_KS_REVISION)
    engine = create_engine(rehearsal_database)

    # The repository baseline represents a fresh current schema. Removing the
    # two new tables reproduces an existing production database that reached
    # the prior revision before Pitcher Ks existed.
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO system_metadata (key, value, updated_at) "
                    "VALUES ('pitcher_ks_rehearsal', 'preserve-me', '2026-08-01')"
                )
            )
            connection.execute(text("DROP TABLE pitcher_k_predictions"))
            connection.execute(text("DROP TABLE pitcher_k_runs"))

        command.upgrade(config, "head")

        assert PITCHER_TABLES <= set(inspect(engine).get_table_names())
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT value FROM system_metadata "
                    "WHERE key = 'pitcher_ks_rehearsal'"
                )
            ).scalar_one() == "preserve-me"
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        assert revision == PITCHER_KS_REVISION
    finally:
        engine.dispose()
