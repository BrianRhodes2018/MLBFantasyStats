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
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
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
# CLEAN UP QUERY PARAMETERS FOR EACH DRIVER
# ---------------------------------------------------------------------------
# Cloud databases like Neon include query parameters in their URLs, e.g.:
#   ?sslmode=require&channel_binding=require
#
# THE PROBLEM: asyncpg and psycopg2 support DIFFERENT parameter names!
#
#   asyncpg (async driver) understands:
#     - ssl=require        (NOT sslmode — that's a libpq/psycopg2 thing)
#
#   psycopg2 (sync driver) understands:
#     - sslmode=require    (standard libpq parameter)
#     - channel_binding=require  (libpq parameter for extra security)
#
#   If we pass "sslmode" or "channel_binding" to asyncpg, it crashes with
#   "unrecognized parameter" error. So we need to:
#     1. For ASYNC_URL: remove sslmode & channel_binding, add ssl=require
#     2. For SYNC_URL: keep sslmode & channel_binding as-is (psycopg2 handles them)
#
# We use Python's urllib.parse to properly parse and rebuild the URLs.
# This is safer than string manipulation because it handles edge cases
# (encoded characters, multiple params, etc.).

is_remote = "localhost" not in ASYNC_URL and "127.0.0.1" not in ASYNC_URL

if is_remote:
    # --- Fix ASYNC_URL for asyncpg ---
    # Parse the URL into its components (scheme, host, path, query, etc.)
    parsed = urlparse(ASYNC_URL)

    # Parse the query string into a dictionary.
    # parse_qs returns {'sslmode': ['require'], 'channel_binding': ['require']}
    # keep_blank_values=True preserves params like "?foo=" with empty values.
    params = parse_qs(parsed.query, keep_blank_values=True)

    # Remove parameters that asyncpg doesn't understand.
    # These are libpq-specific params that psycopg2 supports but asyncpg doesn't.
    params.pop("sslmode", None)          # asyncpg uses "ssl" instead
    params.pop("channel_binding", None)  # libpq-only, asyncpg doesn't support this

    # Add the asyncpg-compatible SSL parameter.
    # "ssl=require" tells asyncpg to use an encrypted connection.
    params["ssl"] = ["require"]

    # Rebuild the query string from the cleaned parameters.
    # doseq=True handles list values (parse_qs returns lists).
    new_query = urlencode(params, doseq=True)

    # Reassemble the full URL with the cleaned query string.
    # urlunparse takes a tuple: (scheme, netloc, path, params, query, fragment)
    # parsed._replace() creates a copy with just the query part changed.
    ASYNC_URL = urlunparse(parsed._replace(query=new_query))

    # --- Fix SYNC_URL for psycopg2 ---
    # psycopg2 understands sslmode and channel_binding natively, so we just
    # need to make sure sslmode is present. Parse and check.
    parsed_sync = urlparse(SYNC_URL)
    sync_params = parse_qs(parsed_sync.query, keep_blank_values=True)

    # Ensure sslmode=require is present (psycopg2's SSL parameter)
    if "sslmode" not in sync_params:
        sync_params["sslmode"] = ["require"]

    new_sync_query = urlencode(sync_params, doseq=True)
    SYNC_URL = urlunparse(parsed_sync._replace(query=new_sync_query))

# For reference, here's what the final URLs look like:
#
#   LOCAL DEVELOPMENT (no changes, no SSL):
#     ASYNC_URL = "postgresql+asyncpg://postgres:admin123@localhost:5432/mlb_db"
#     SYNC_URL  = "postgresql://postgres:admin123@localhost:5432/mlb_db"
#
#   PRODUCTION with Neon URL like:
#     postgresql://user:pass@ep-cool.neon.tech/neondb?sslmode=require&channel_binding=require
#
#     ASYNC_URL = "postgresql+asyncpg://user:pass@ep-cool.neon.tech/neondb?ssl=require"
#                 (sslmode → ssl, channel_binding removed — asyncpg compatible)
#     SYNC_URL  = "postgresql://user:pass@ep-cool.neon.tech/neondb?sslmode=require&channel_binding=require"
#                 (kept as-is — psycopg2 compatible)

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
