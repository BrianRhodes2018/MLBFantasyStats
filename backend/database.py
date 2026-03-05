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
"""

import os
from dotenv import load_dotenv  # Loads variables from a .env file into os.environ

# load_dotenv() reads a .env file in the project root (if it exists) and adds
# its key=value pairs to the environment. This is convenient for local development
# so you don't have to set environment variables manually each time.
load_dotenv()

from databases import Database           # Async database interface
from sqlalchemy import create_engine, MetaData  # Sync engine + metadata registry

# os.environ.get() tries to read DATABASE_URL from environment variables.
# If it's not set, we fall back to a default local PostgreSQL URL.
# The "+asyncpg" part tells SQLAlchemy/databases to use the asyncpg driver.
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:admin123@localhost:5432/mlb_db"
)

# The async Database object — this is what we use for all queries in our endpoints.
# It manages a connection pool internally and supports await-based queries.
database = Database(DATABASE_URL)

# MetaData is a registry that holds Table objects. When we define tables in models.py,
# they register themselves with this metadata instance. Later, we can call
# metadata.create_all(engine) to create all registered tables in the database.
metadata = MetaData()

# Synchronous engine — ONLY used for metadata.create_all() at startup.
# We replace "+asyncpg" with plain psycopg2 (sync driver) because create_all()
# is a synchronous operation that doesn't support async.
engine = create_engine(DATABASE_URL.replace("+asyncpg", ""))
