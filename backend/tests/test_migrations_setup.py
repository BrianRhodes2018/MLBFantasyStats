"""Guards for the Alembic migration setup."""

from pathlib import Path
from unittest.mock import Mock

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _script_directory() -> ScriptDirectory:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return ScriptDirectory.from_config(config)


def test_single_migration_head():
    """Two heads mean two migrations were written from the same parent
    (usually parallel branches) — upgrades would refuse to run."""
    assert len(_script_directory().get_heads()) == 1


def test_run_table_grants_follow_existing_hit_pick_writer_grants():
    """The daily least-privilege writer must be able to append run records."""
    script = _script_directory()
    head = script.get_revision(script.get_current_head())
    source = Path(head.path).read_text(encoding="utf-8")
    assert "information_schema.table_privileges" in source
    assert "GRANT %s ON TABLE hit_pick_runs" in source
    assert "grantee <> current_user" in source


def test_baseline_creates_every_model_table():
    """The baseline migration must create every table models.py defines,
    so a fresh database matches an evolved one."""
    import sys

    sys.path.insert(0, str(BACKEND_DIR))
    from database import metadata
    import models  # noqa: F401  (registers tables on metadata)

    script = _script_directory()
    base_revision = next(
        rev for rev in script.walk_revisions() if rev.down_revision is None
    )
    baseline_source = Path(script.get_revision(base_revision.revision).path).read_text(
        encoding="utf-8"
    )
    for table_name in metadata.tables:
        assert f"op.create_table('{table_name}'" in baseline_source, (
            f"{table_name} missing from baseline migration"
        )


def test_legacy_database_stamps_baseline_before_upgrading(monkeypatch):
    """A legacy database must not skip migrations added after the baseline."""
    import migrations

    inspector = Mock()
    inspector.has_table.side_effect = lambda name: name == "players"
    monkeypatch.setattr(migrations, "inspect", lambda _engine: inspector)
    stamp = Mock()
    upgrade = Mock()
    monkeypatch.setattr(migrations.command, "stamp", stamp)
    monkeypatch.setattr(migrations.command, "upgrade", upgrade)

    migrations.run_migrations()

    stamp.assert_called_once()
    assert stamp.call_args.args[1] == "c8a75a27355e"
    upgrade.assert_called_once()
    assert upgrade.call_args.args[1] == "head"
