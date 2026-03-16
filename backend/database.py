"""
database.py - Database Connection Configuration
================================================

This module sets up the async database connection for our FastAPI application.

Key concepts:
- We use the `databases` library for ASYNC database access (non-blocking I/O).
  This is different from standard SQLAlchemy which is synchronous.
- `asyncpg` is the async PostgreSQL driver that `databases` uses under the hood.
- We also create a synchronous SQLAlchemy `engine` — this is ONLY used for
  `metadata.create_all()` to create tables on startup. All actual queries
  go through the async `database` object.
- `MetaData()` is SQLAlchemy's container for table definitions. Tables defined
  elsewhere (models.py) register themselves with this metadata object.

Environment Variables:
- DATABASE_URL: Full PostgreSQL connection string. Falls back to a local default
  if not set. Format: postgresql+asyncpg://user:password@host:port/dbname

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
from dotenv import load_dotenv  # Loads variables from a .env file into os.environ

# load_dotenv() reads a .env file in the project root (if it exists) and adds
# its key=value pairs to the environment. This is convenient for local development
# so you don't have to set environment variables manually each time.
load_dotenv()

from databases import Database           # Async database interface
from sqlalchemy import create_engine, MetaData  # Sync engine + metadata registry

# ---------------------------------------------------------------------------
# READ THE DATABASE URL FROM ENVIRONMENT
# ---------------------------------------------------------------------------
# os.environ.get() tries to read DATABASE_URL from environment variables.
# If it's not set, we fall back to a default local PostgreSQL URL.
# The "+asyncpg" part tells SQLAlchemy/databases to use the asyncpg driver.
raw_url = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:admin123@localhost:5432/mlb_db"
)

# ---------------------------------------------------------------------------
# NORMALIZE THE URL FOR DIFFERENT DRIVERS
# ---------------------------------------------------------------------------
# Cloud providers give URLs in different formats. We need to handle them all:
#
#   What you might get from Neon/Render/Supabase:
#     "postgres://user:pass@host/db"          ← shorthand prefix
#     "postgresql://user:pass@host/db"        ← full prefix, no driver
#     "postgresql+asyncpg://user:pass@host/db" ← already correct for async
#
#   What we need:
#     ASYNC_URL  = "postgresql+asyncpg://user:pass@host/db"  (for the `databases` library)
#     SYNC_URL   = "postgresql://user:pass@host/db"          (for SQLAlchemy create_all)
#
# Step 1: Normalize to the async format (postgresql+asyncpg://)
# We check for the most specific prefix first to avoid double-replacing.

if raw_url.startswith("postgresql+asyncpg://"):
    # Already in the correct async format — no conversion needed.
    ASYNC_URL = raw_url
elif raw_url.startswith("postgresql://"):
    # Has the full "postgresql://" prefix but no driver specified.
    # Add "+asyncpg" to tell the databases library which driver to use.
    ASYNC_URL = raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
elif raw_url.startswith("postgres://"):
    # Shorthand "postgres://" (common from Heroku, Render, Neon).
    # Replace with the full async prefix.
    ASYNC_URL = raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
else:
    # Unknown format — use as-is and hope for the best.
    # This shouldn't happen with standard PostgreSQL URLs.
    ASYNC_URL = raw_url

# Step 2: Create the sync URL for SQLAlchemy's create_engine().
# Just strip "+asyncpg" so we get plain "postgresql://" which uses psycopg2.
SYNC_URL = ASYNC_URL.replace("+asyncpg", "")

# ---------------------------------------------------------------------------
# ADD SSL FOR REMOTE DATABASES
# ---------------------------------------------------------------------------
# Cloud databases like Neon REQUIRE encrypted (SSL) connections.
# Local development (localhost) doesn't need SSL.
#
# How to tell if we're connecting to a remote database:
#   - If "localhost" or "127.0.0.1" is in the URL → local, no SSL needed
#   - Otherwise → remote, SSL required
#
# The tricky part: asyncpg and psycopg2 use DIFFERENT SSL parameter names:
#   - asyncpg (async driver):   ?ssl=require
#   - psycopg2 (sync driver):   ?sslmode=require
#
# We also need to handle URLs that already have query parameters (?foo=bar)
# by using "&" instead of "?" for additional parameters.

is_remote = "localhost" not in ASYNC_URL and "127.0.0.1" not in ASYNC_URL

if is_remote:
    # Add SSL parameter for the ASYNC driver (asyncpg uses "ssl" not "sslmode")
    if "?" in ASYNC_URL:
        # URL already has query params — append with &
        ASYNC_URL += "&ssl=require"
    else:
        # No existing query params — start with ?
        ASYNC_URL += "?ssl=require"

    # Add SSL parameter for the SYNC driver (psycopg2 uses "sslmode")
    if "?" in SYNC_URL:
        SYNC_URL += "&sslmode=require"
    else:
        SYNC_URL += "?sslmode=require"

# For reference, here's what the final URLs look like:
#
#   LOCAL DEVELOPMENT:
#     ASYNC_URL = "postgresql+asyncpg://postgres:admin123@localhost:5432/mlb_db"
#     SYNC_URL  = "postgresql://postgres:admin123@localhost:5432/mlb_db"
#
#   PRODUCTION (Neon example):
#     ASYNC_URL = "postgresql+asyncpg://user:pass@ep-cool-name.neon.tech/neondb?ssl=require"
#     SYNC_URL  = "postgresql://user:pass@ep-cool-name.neon.tech/neondb?sslmode=require"

# Keep DATABASE_URL pointing to the async version for backward compatibility
# (main.py and other modules import DATABASE_URL from this file).
DATABASE_URL = ASYNC_URL

# ---------------------------------------------------------------------------
# CREATE DATABASE OBJECTS
# ---------------------------------------------------------------------------

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
