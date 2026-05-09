"""
migrations.py - Lightweight Schema Migration Helper
====================================================

Adds any newly-introduced columns to existing tables. This is a small
manual alternative to Alembic — it inspects the live schema and runs
ALTER TABLE for any missing columns.

Why this lives in its own module:
    Both `main.py` (FastAPI startup) and `daily_update.py` (GitHub
    Actions cron) need to ensure the schema is up-to-date before
    reading/writing data. Extracting the logic here avoids importing
    main.py from the daily-update entry point (which would pull in
    the entire FastAPI app and its dependencies).

Calling `run_migrations()` is idempotent — it's safe to call on every
startup. Existing columns are skipped, only missing ones are added.
"""

from sqlalchemy import inspect, text
from database import engine


def run_migrations():
    """
    Check for missing columns and add them to existing tables.

    Inspects the live database schema, compares it against the expected
    column lists below, and runs ALTER TABLE for any missing columns.

    Add new entries to the missing_columns / pitcher_missing / fl_missing
    dicts when introducing new columns in models.py.
    """
    inspector = inspect(engine)

    # --- Players table migrations ---
    if inspector.has_table("players"):
        existing_columns = [col["name"] for col in inspector.get_columns("players")]
        missing_columns = {
            "position": "VARCHAR(10)",
            "runs": "INTEGER",
            "strikeouts": "INTEGER",
            "total_bases": "INTEGER",
            "at_bats": "INTEGER",
            "mlb_id": "INTEGER",          # MLB Stats API player ID for game log linking
            "walks": "INTEGER",           # BB - Bases on balls (needed for OBP calculation)
            "hit_by_pitch": "INTEGER",    # HBP - Hit by pitch (needed for OBP calculation)
            "sacrifice_flies": "INTEGER", # SF - Sacrifice flies (needed for OBP calculation)
            "hits": "INTEGER",            # H - Total hits (needed for fantasy points)
            "doubles": "INTEGER",         # 2B - Doubles (needed for fantasy points)
            "triples": "INTEGER",         # 3B - Triples (needed for fantasy points)
            "caught_stealing": "INTEGER", # CS - Caught stealing (needed for fantasy points)
            "bats": "VARCHAR(2)",         # Batting handedness: 'R', 'L', or 'S' (switch)
        }

        with engine.connect() as conn:
            for col_name, col_type in missing_columns.items():
                if col_name not in existing_columns:
                    conn.execute(text(f"ALTER TABLE players ADD COLUMN {col_name} {col_type}"))
            conn.commit()

    # --- Pitchers table migrations ---
    if inspector.has_table("pitchers"):
        existing_pitcher_cols = [col["name"] for col in inspector.get_columns("pitchers")]
        pitcher_missing = {
            "quality_starts": "INTEGER",  # QS - Quality Starts
            "mlb_id": "INTEGER",          # MLB Stats API player ID
            "throws": "VARCHAR(2)",       # Throwing handedness: 'R' or 'L'
            "hit_by_pitch": "INTEGER",    # HBP - Hit By Pitch (needed for true FIP)
        }

        with engine.connect() as conn:
            for col_name, col_type in pitcher_missing.items():
                if col_name not in existing_pitcher_cols:
                    conn.execute(text(f"ALTER TABLE pitchers ADD COLUMN {col_name} {col_type}"))
            conn.commit()

    # --- Pitcher game logs table migrations ---
    # Add HBP at the per-game level so rolling FIP windows can be computed.
    if inspector.has_table("pitcher_game_logs"):
        existing_pgl_cols = [col["name"] for col in inspector.get_columns("pitcher_game_logs")]
        pgl_missing = {
            "hit_by_pitch": "INTEGER DEFAULT 0",  # HBP - per-game (for rolling FIP)
        }

        with engine.connect() as conn:
            for col_name, col_type in pgl_missing.items():
                if col_name not in existing_pgl_cols:
                    conn.execute(text(f"ALTER TABLE pitcher_game_logs ADD COLUMN {col_name} {col_type}"))
            conn.commit()

    # --- Fantasy leagues table migrations ---
    # Add Yahoo-specific columns for the Yahoo Fantasy integration.
    # The provider column identifies whether a league is ESPN or Yahoo,
    # and the yahoo_* columns store OAuth tokens and league keys.
    # DEFAULT 'espn' ensures existing rows are tagged as ESPN leagues.
    if inspector.has_table("fantasy_leagues"):
        existing_fl_cols = [col["name"] for col in inspector.get_columns("fantasy_leagues")]
        fl_missing = {
            "provider": "VARCHAR(20) DEFAULT 'espn'",      # "espn" or "yahoo"
            "yahoo_league_key": "VARCHAR(50)",              # e.g. "431.l.123456"
            "yahoo_access_token": "VARCHAR(2000)",          # OAuth access token
            "yahoo_refresh_token": "VARCHAR(2000)",         # OAuth refresh token
            "yahoo_token_expires_at": "VARCHAR(30)",        # Token expiry timestamp
        }

        with engine.connect() as conn:
            for col_name, col_type in fl_missing.items():
                if col_name not in existing_fl_cols:
                    conn.execute(text(f"ALTER TABLE fantasy_leagues ADD COLUMN {col_name} {col_type}"))
            conn.commit()

        # Also make league_id nullable for Yahoo leagues (they use yahoo_league_key instead).
        # This ALTER only needs to run once; it's safe to re-run (no-op if already nullable).
        with engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE fantasy_leagues ALTER COLUMN league_id DROP NOT NULL"))
                conn.commit()
            except Exception:
                conn.rollback()  # Silently ignore if column is already nullable
