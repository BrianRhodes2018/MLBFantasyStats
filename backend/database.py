"""
database.py - Database Connection Configuration
================================================

This module sets up the async database connections for our FastAPI application.

Key concepts:
- We use the `databases` library for ASYNC database access (non-blocking I/O).
  This is different from standard SQLAlchemy which is synchronous.
- `asyncpg` is the async PostgreSQL driver that `databases` uses under the hood.
- We also create a synchronous SQLAlchemy `engine` — this is ONLY used for
  `metadata.create_all()` to create tables on startup. All actual queries
  go through the async `database` object.
- `MetaData()` is SQLAlchemy's container for table definitions. Tables defined
  elsewhere (models.py) register themselves with this metadata object.

Multi-Season Support:
  The app supports multiple database connections for different seasons.
  The primary database (DATABASE_URL) holds the current season's live data,
  while optional snapshot databases (DATABASE_URL_2025, etc.) hold frozen
  historical data from previous seasons.

  The get_db(season) helper routes queries to the correct connection:
    - get_db(None)    → current season (default)
    - get_db("2025")  → 2025 historical snapshot

Environment Variables:
- DATABASE_URL: Primary database (current season). Falls back to local default.
- DATABASE_URL_2025: Optional Neon branch with frozen 2025 season data.

Production URL Handling:
  Cloud database providers (Neon, Render, Supabase, etc.) typically give you a
  connection string that starts with "postgres://" or "postgresql://", like:
    postgres://user:pass@ep-cool-name-123.us-east-2.aws.neon.tech/neondb

  But our code needs TWO different formats:
    1. Async driver (asyncpg):  postgresql+asyncpg://...
    2. Sync driver (psycopg2):  postgresql://...

  This file automatically converts whatever URL you provide into both formats.
  It also adds SSL when connecting to remote databases (required by Neon, etc.).
"""

import os
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from dotenv import load_dotenv  # Loads variables from a .env file into os.environ

# load_dotenv() reads a .env file in the project root (if it exists) and adds
# its key=value pairs to the environment. This is convenient for local development
# so you don't have to set environment variables manually each time.
load_dotenv()

from databases import Database           # Async database interface
from sqlalchemy import create_engine, MetaData  # Sync engine + metadata registry


# ---------------------------------------------------------------------------
# URL NORMALIZATION HELPER
# ---------------------------------------------------------------------------
# Extracted into a reusable function since we need to normalize URLs for
# both the primary database and any snapshot databases (2025, etc.).

def normalize_database_url(raw_url):
    """
    Convert any PostgreSQL connection string into the two formats we need:
      - async_url: postgresql+asyncpg://... (for the `databases` library)
      - sync_url:  postgresql://...         (for SQLAlchemy create_engine)

    Also cleans up query parameters for each driver (asyncpg vs psycopg2
    understand different SSL parameter names).

    Returns: (async_url, sync_url) tuple
    """
    # Step 1: Normalize to async format (postgresql+asyncpg://)
    if raw_url.startswith("postgresql+asyncpg://"):
        async_url = raw_url
    elif raw_url.startswith("postgresql://"):
        async_url = raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif raw_url.startswith("postgres://"):
        async_url = raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
    else:
        async_url = raw_url

    # Step 2: Create sync URL by stripping the async driver
    sync_url = async_url.replace("+asyncpg", "")

    # Step 3: Fix query parameters for remote databases
    is_remote = "localhost" not in async_url and "127.0.0.1" not in async_url

    if is_remote:
        # Fix ASYNC_URL for asyncpg — remove sslmode/channel_binding, add ssl=require
        parsed = urlparse(async_url)
        params = parse_qs(parsed.query, keep_blank_values=True)
        params.pop("sslmode", None)
        params.pop("channel_binding", None)
        params["ssl"] = ["require"]
        async_url = urlunparse(parsed._replace(query=urlencode(params, doseq=True)))

        # Fix SYNC_URL for psycopg2 — ensure sslmode=require is present
        parsed_sync = urlparse(sync_url)
        sync_params = parse_qs(parsed_sync.query, keep_blank_values=True)
        if "sslmode" not in sync_params:
            sync_params["sslmode"] = ["require"]
        sync_url = urlunparse(parsed_sync._replace(query=urlencode(sync_params, doseq=True)))

    return async_url, sync_url


# ---------------------------------------------------------------------------
# PRIMARY DATABASE (Current Season)
# ---------------------------------------------------------------------------
# This is the main database that gets daily updates during the season.
# Falls back to a local PostgreSQL URL for development.

raw_url = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:admin123@localhost:5432/mlb_db"
)

ASYNC_URL, SYNC_URL = normalize_database_url(raw_url)

# Keep DATABASE_URL pointing to the async version for backward compatibility
# (main.py and other modules import DATABASE_URL from this file).
DATABASE_URL = ASYNC_URL

# The async Database object — this is what we use for all queries in our endpoints.
# It manages a connection pool internally and supports await-based queries.
database = Database(DATABASE_URL)

# MetaData is a registry that holds Table objects. When we define tables in models.py,
# they register themselves with this metadata instance. Later, we can call
# metadata.create_all(engine) to create all registered tables in the database.
metadata = MetaData()

# Synchronous engine — ONLY used for metadata.create_all() at startup.
# Uses the SYNC_URL (postgresql:// with psycopg2 driver) because create_all()
# is a synchronous operation that doesn't support async.
engine = create_engine(SYNC_URL)


# ---------------------------------------------------------------------------
# SNAPSHOT DATABASES (Historical Seasons)
# ---------------------------------------------------------------------------
# Optional connections to frozen Neon branches containing historical data.
# Each snapshot is a read-only copy-on-write branch created from main at a
# point in time. They have their own connection strings.
#
# To add a new season snapshot:
#   1. Create a Neon branch from main (freezes current data)
#   2. Set DATABASE_URL_<YEAR> in your environment / Render dashboard
#   3. Add it to the snapshot_databases dict below
#
# If the env var isn't set, that season simply won't be available.

snapshot_databases = {}

# 2025 season snapshot — frozen historical data from the 2025 season
raw_url_2025 = os.environ.get("DATABASE_URL_2025")
if raw_url_2025:
    async_url_2025, _ = normalize_database_url(raw_url_2025)
    snapshot_databases["2025"] = Database(async_url_2025)


def get_db(season=None):
    """
    Route queries to the correct database connection based on season.

    Args:
        season: Season year as string (e.g., "2025") or None for current season.

    Returns:
        The appropriate async Database object.

    Examples:
        db = get_db()          # Current season (2026)
        db = get_db("2025")    # 2025 historical snapshot
        db = get_db(None)      # Same as get_db() — current season
    """
    if season and season in snapshot_databases:
        return snapshot_databases[season]
    return database


# List of available seasons (for the frontend to know what's available)
available_seasons = sorted(snapshot_databases.keys())
