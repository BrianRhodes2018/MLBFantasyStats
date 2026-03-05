"""
main.py - FastAPI Application Entry Point
==========================================

This is the main application file that defines our FastAPI web server.

Key concepts demonstrated:
- FastAPI app creation and endpoint routing
- CORS (Cross-Origin Resource Sharing) middleware for frontend communication
- Async database operations with the `databases` library
- Query parameters (Optional) for flexible search/filter endpoints
- Polars DataFrame operations for computing statistics:
  - Column arithmetic (e.g., home_runs * ops)
  - Aggregations with .mean()
  - GroupBy operations with .group_by()
  - Column transformations with .with_columns()
  - Filtering with .filter() and dynamic expression building
  - String matching with .str.to_lowercase().str.contains()
  - Range filtering with >= and <= operators
- Pydantic response models for automatic validation
- Consistent API response format with code + message (Swagger-style)

To run this server:
    cd backend
    uvicorn main:app --reload

The --reload flag enables auto-restart when you edit code (development only).
The server will start at http://localhost:8000 by default.
API docs are auto-generated at http://localhost:8000/docs (Swagger UI).
"""

import os
from typing import Optional
from datetime import datetime, timedelta
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from database import database, engine, metadata
from models import players, pitchers, batter_game_logs, pitcher_game_logs
from schemas import PlayerIn, PlayerOut, PlayerUpdate, PitcherIn, PitcherOut, PitcherUpdate, ApiResponse
import polars as pl
from sqlalchemy import text, inspect

# Create the FastAPI application instance.
# This object is what uvicorn serves. All routes are registered on it.
app = FastAPI(
    title="MLB Player Stats API",
    description="A FastAPI backend serving MLB player statistics with Polars-computed analytics",
)

# ---------------------------------------------------------------------------
# CORS Middleware
# ---------------------------------------------------------------------------
# CORS (Cross-Origin Resource Sharing) controls which websites can make
# requests to this API. Without this, a React app running on localhost:5173
# would be BLOCKED from fetching data from this API on localhost:8000.
#
# This is a browser security feature — the browser checks if the server
# explicitly allows requests from the frontend's origin (protocol + domain + port).
#
# For PRODUCTION, we read allowed origins from the CORS_ORIGINS environment
# variable (comma-separated). This lets us add the Vercel frontend URL
# without hardcoding it. Falls back to the Vite dev server for local dev.
#
# allow_origins: List of allowed frontend URLs.
# allow_methods: ["*"] allows GET, POST, PUT, DELETE, etc.
# allow_headers: ["*"] allows any HTTP headers (like Content-Type).
# allow_credentials: Allows cookies/auth headers to be sent cross-origin.

# Build the allowed origins list from environment variable + localhost default.
# CORS_ORIGINS env var should be comma-separated, e.g.:
#   "https://your-app.vercel.app,https://custom-domain.com"
cors_origins = ["http://localhost:5173"]  # Always allow local dev server
extra_origins = os.environ.get("CORS_ORIGINS", "")
if extra_origins:
    # Split on commas and strip whitespace from each origin
    cors_origins.extend([origin.strip() for origin in extra_origins.split(",") if origin.strip()])

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Database Table Creation & Migration
# ---------------------------------------------------------------------------
# metadata.create_all() inspects all Table objects registered with `metadata`
# (defined in models.py) and creates them in the database if they don't exist.
# This runs synchronously using the sync `engine` (not the async `database`).
# It's safe to call on every startup — it won't drop/recreate existing tables.
#
# IMPORTANT: create_all() does NOT add new columns to existing tables.
# If the table already exists without the 'position' column, we need to
# manually ALTER TABLE to add it. This is a simple migration approach.
# In production, you'd use a migration tool like Alembic for this.
metadata.create_all(bind=engine)


def run_migrations():
    """
    Check for missing columns and add them to existing tables.

    This is a lightweight migration approach. It uses SQLAlchemy's inspect()
    to check which columns currently exist in the database table, then
    runs ALTER TABLE to add any that are missing.

    In a production app, you'd use Alembic (SQLAlchemy's migration tool)
    for this. But for learning purposes, this manual approach shows you
    exactly what's happening at the SQL level.

    Key concept: DDL (Data Definition Language) vs DML (Data Manipulation Language)
    - DDL changes the table STRUCTURE (ALTER TABLE, CREATE TABLE, DROP TABLE)
    - DML changes the table DATA (INSERT, UPDATE, DELETE, SELECT)
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
        }

        with engine.connect() as conn:
            for col_name, col_type in pitcher_missing.items():
                if col_name not in existing_pitcher_cols:
                    conn.execute(text(f"ALTER TABLE pitchers ADD COLUMN {col_name} {col_type}"))
            conn.commit()


# Run migrations on import (before the server starts handling requests)
run_migrations()


# ---------------------------------------------------------------------------
# Startup & Shutdown Events
# ---------------------------------------------------------------------------
# These lifecycle hooks run when the server starts and stops.
# On startup: connect to the database and seed sample data if the table is empty.
# On shutdown: cleanly disconnect from the database.

@app.on_event("startup")
async def startup():
    """Connect to the database on server startup and seed sample data if empty."""
    await database.connect()
    # Check if any players exist — if not, populate with sample data
    existing = await database.fetch_all(players.select())
    if not existing:
        await populate_sample_data()


@app.on_event("shutdown")
async def shutdown():
    """Disconnect from the database when the server shuts down."""
    await database.disconnect()


# ===========================================================================
# HELPER: Convert Database Rows to Polars DataFrame
# ===========================================================================

def rows_to_dataframe(rows):
    """
    Convert database Row objects to a Polars DataFrame.

    The `databases` library returns Record objects from fetch_all().
    Newer versions of Polars (1.x+) don't automatically understand
    these Record objects — passing them directly to pl.DataFrame()
    produces garbled column names like "column_0", "column_1", etc.

    The fix: convert each Record to a plain Python dict using ._mapping,
    which exposes the column names and values. Polars knows how to
    build a DataFrame from a list of dicts.

    Args:
        rows: List of database Record objects from database.fetch_all()

    Returns:
        pl.DataFrame: A properly structured DataFrame with correct column names
    """
    return pl.DataFrame([dict(r._mapping) for r in rows])


# ===========================================================================
# HELPER: Get Numeric Stat Columns
# ===========================================================================

def get_numeric_stat_columns():
    """
    Return the list of numeric stat column names from the players table.

    This function inspects the SQLAlchemy table definition to dynamically
    find all Float and Integer columns, excluding non-stat columns:
    - 'id': Auto-increment primary key (database internal)
    - 'mlb_id': MLB Stats API player ID (used internally to link game logs,
       not a meaningful stat for users)

    Why dynamic? If you add a new stat column to models.py (e.g., "walks"),
    it will automatically be included in search filters and aggregations
    without changing any endpoint code. This is the DRY principle
    (Don't Repeat Yourself) in action.

    Returns:
        list[str]: Column names like ["batting_average", "home_runs", "rbi", ...]
    """
    from sqlalchemy import Float, Integer
    return [
        col.name for col in players.columns
        if isinstance(col.type, (Float, Integer)) and col.name not in ("id", "mlb_id")
    ]


# ===========================================================================
# API ENDPOINTS
# ===========================================================================

@app.post("/players/", response_model=ApiResponse)
async def create_player(player: PlayerIn):
    """
    Create a new player in the database.

    How it works:
    1. FastAPI automatically validates the request body against PlayerIn schema.
       If validation fails, a 422 error is returned before this function runs.
    2. player.dict() converts the Pydantic model to a plain dictionary.
    3. players.insert().values(**dict) builds a SQL INSERT statement.
    4. database.execute() runs the query and returns the new row's ID.
    5. We return a consistent ApiResponse with code, message, and the player data.

    Response format (Swagger-style):
        { "code": 201, "message": "Player 'Aaron Judge' created successfully", "data": {...} }

    Args:
        player: A PlayerIn Pydantic model parsed from the JSON request body.

    Returns:
        ApiResponse with code 201 and the created player data including their ID.
    """
    query = players.insert().values(**player.dict())
    player_id = await database.execute(query)

    # Return a structured response with code and message — like Swagger UI shows.
    # Code 201 means "Created" — the standard HTTP status for successful resource creation.
    return ApiResponse(
        code=201,
        message=f"Player '{player.name}' created successfully",
        data={**player.dict(), "id": player_id}
    )


@app.put("/players/{player_id}", response_model=ApiResponse)
async def update_player(player_id: int, player: PlayerUpdate):
    """
    Update an existing player's data.

    This endpoint demonstrates several FastAPI and SQLAlchemy concepts:

    FastAPI concepts:
    - Path parameters: {player_id} in the URL becomes a function argument.
      FastAPI automatically extracts it from the URL and casts it to int.
      Example: PUT /players/3 → player_id=3
    - Request body + path parameter: FastAPI can handle BOTH in one endpoint.
      The path parameter (player_id) comes from the URL, and the request body
      (player) comes from the JSON payload. FastAPI knows the difference because
      path params are simple types (int, str) and body params are Pydantic models.
    - response_model=ApiResponse: Tells FastAPI to validate and document the
      response format in the auto-generated Swagger UI docs at /docs.

    Pydantic concepts:
    - player.dict(exclude_unset=True): The KEY to partial updates.
      Returns ONLY the fields the client explicitly sent in the JSON body.
      See the PlayerUpdate schema docstring in schemas.py for a detailed explanation.

    SQLAlchemy concepts:
    - players.select().where(): Builds a SELECT ... WHERE id = ? query.
    - players.c.id: The 'c' stands for 'columns' — it's how SQLAlchemy Core
      accesses column objects for building WHERE clauses.
    - players.update().where().values(): Builds an UPDATE ... SET ... WHERE query.
    - The **update_data unpacking passes the dict as keyword arguments:
      .values(**{"home_runs": 55}) becomes .values(home_runs=55)
    - database.fetch_one(): Returns a single row (or None if not found).
      Different from fetch_all() which returns a list of all matching rows.

    The endpoint follows this flow:
    1. Extract which fields the client wants to change (exclude_unset)
    2. Validate: are there any fields to update? (400 if empty)
    3. Validate: does the player exist? (404 if not found)
    4. Execute the UPDATE query
    5. Fetch the updated row to confirm the changes
    6. Return the updated data in a consistent ApiResponse format

    SQL equivalent of step 4:
        UPDATE players SET home_runs = 55 WHERE id = 3

    Args:
        player_id: The player's database ID from the URL path.
        player: A PlayerUpdate Pydantic model with the fields to change.

    Returns:
        ApiResponse with code 200 and the updated player data, or 404 if not found.
    """
    # -----------------------------------------------------------------------
    # Step 1: Extract only the fields the client sent
    # -----------------------------------------------------------------------
    # player.dict(exclude_unset=True) is the Pydantic method that makes
    # partial updates work. It returns a dict of ONLY the fields that were
    # explicitly included in the JSON request body.
    #
    # Example: If the client sends {"home_runs": 55, "rbi": 120}
    #   player.dict()                  → {"name": None, "team": None, ..., "home_runs": 55, "rbi": 120, ...}
    #   player.dict(exclude_unset=True) → {"home_runs": 55, "rbi": 120}
    #
    # Without exclude_unset, we'd overwrite all fields with None — destroying data!
    update_data = player.dict(exclude_unset=True)

    # -----------------------------------------------------------------------
    # Step 2: Validate that the client sent at least one field
    # -----------------------------------------------------------------------
    # If the client sends an empty body {} or no recognized fields,
    # update_data will be empty. There's nothing to update.
    if not update_data:
        return ApiResponse(code=400, message="No fields provided to update", data=None)

    # -----------------------------------------------------------------------
    # Step 3: Check if the player exists
    # -----------------------------------------------------------------------
    # Before running UPDATE, we verify the player ID exists in the database.
    # This is a defensive check — without it, the UPDATE would silently succeed
    # but affect 0 rows, and the user wouldn't know why nothing changed.
    #
    # players.select().where(players.c.id == player_id) builds:
    #   SELECT * FROM players WHERE id = {player_id}
    #
    # players.c.id accesses the 'id' column object. The 'c' attribute stands
    # for 'columns' and is SQLAlchemy Core's way of referencing table columns.
    existing = await database.fetch_one(
        players.select().where(players.c.id == player_id)
    )
    if not existing:
        return ApiResponse(code=404, message=f"Player with id {player_id} not found", data=None)

    # -----------------------------------------------------------------------
    # Step 4: Build and execute the UPDATE query
    # -----------------------------------------------------------------------
    # players.update() starts building an UPDATE statement.
    # .where(players.c.id == player_id) adds: WHERE id = {player_id}
    # .values(**update_data) adds: SET col1 = val1, col2 = val2, ...
    #
    # The ** operator unpacks the dict into keyword arguments:
    #   .values(**{"home_runs": 55, "rbi": 120})
    #   becomes: .values(home_runs=55, rbi=120)
    #
    # Full SQL equivalent: UPDATE players SET home_runs = 55, rbi = 120 WHERE id = 3
    query = players.update().where(players.c.id == player_id).values(**update_data)
    await database.execute(query)

    # -----------------------------------------------------------------------
    # Step 5: Fetch the updated row to return in the response
    # -----------------------------------------------------------------------
    # We re-fetch the player after the update to get all current values.
    # This confirms the update succeeded and gives the frontend the
    # complete, up-to-date player data to display.
    #
    # ._mapping is used to convert the database Row object to a dict-like
    # interface that exposes column names. dict() then converts it to a
    # plain Python dict that FastAPI can serialize to JSON.
    updated = await database.fetch_one(
        players.select().where(players.c.id == player_id)
    )

    return ApiResponse(
        code=200,
        message=f"Player '{updated._mapping['name']}' updated successfully",
        data=dict(updated._mapping)
    )


@app.get("/players/", response_model=list[PlayerOut])
async def get_players():
    """
    Retrieve all players from the database.

    How it works:
    1. players.select() builds a SQL SELECT * FROM players query.
    2. database.fetch_all() executes it and returns a list of Row objects.
    3. FastAPI serializes each row using the PlayerOut schema.
    4. response_model=list[PlayerOut] tells FastAPI the response is a JSON array.

    Returns:
        A JSON array of all players with their stats.
    """
    query = players.select()
    results = await database.fetch_all(query)
    return results


@app.get("/players/search", response_model=ApiResponse)
async def search_players(
    # ---------------------------------------------------------------------------
    # Query Parameters with FastAPI
    # ---------------------------------------------------------------------------
    # team and position are explicit parameters. All numeric stat filters
    # (min_*/max_*) are handled dynamically via the Request object so that
    # adding a new stat column to models.py automatically makes it filterable
    # — no endpoint code changes needed.
    # ---------------------------------------------------------------------------
    request: Request,
    team: Optional[str] = Query(None, description="Filter by team name (case-insensitive partial match)"),
    position: Optional[str] = Query(None, description="Filter by position (case-insensitive, e.g. 'RF', 'DH')"),
):
    """
    Search and filter players using Polars DataFrame operations.

    This endpoint demonstrates Polars' powerful .filter() method for
    conditionally selecting rows from a DataFrame. Filters are built
    dynamically based on which query parameters are provided.

    New Polars concepts:
    - .filter(expression): Keep only rows where the expression is True.
      Similar to SQL WHERE clause, but using Polars expressions.
    - pl.col("team").str.to_lowercase().str.contains("dod"):
      Case-insensitive string matching using Polars string operations.
    - pl.col("home_runs") >= 30: Comparison operators create boolean expressions.
    - Chaining filters: Each .filter() call narrows the results further,
      equivalent to multiple AND conditions in SQL WHERE.

    The filters are COMPOSABLE — you can combine any subset:
    - Just team: /players/search?team=dodgers
    - Just a stat range: /players/search?min_home_runs=40
    - Position + stats: /players/search?position=RF&min_ops=0.9&max_ops=1.1
    - Computed stat: /players/search?min_obp=0.350 (OBP is computed on the fly)
    - Everything: /players/search?team=yankees&position=RF&min_home_runs=20&max_ops=1.0

    Returns:
        ApiResponse with code 200 and a "results" list of matching players,
        plus a "count" showing how many matched.
    """
    query = players.select()
    rows = await database.fetch_all(query)
    df = rows_to_dataframe(rows)

    if df.is_empty():
        return ApiResponse(code=200, message="No players in database", data={"results": [], "count": 0})

    # -----------------------------------------------------------------------
    # Build filters dynamically
    # -----------------------------------------------------------------------
    # We start with the full DataFrame and progressively narrow it down.
    # Each filter is only applied if the corresponding query parameter was provided.
    # This is a common pattern: build a list of conditions, then apply them all.

    # --- Team filter (case-insensitive partial match) ---
    # pl.col("team").str.to_lowercase() converts "Yankees" -> "yankees"
    # .str.contains() checks if the lowercase team name contains the search term.
    # This means searching "dod" would match "Dodgers".
    if team is not None:
        df = df.filter(
            pl.col("team").str.to_lowercase().str.contains(team.lower())
        )

    # --- Position filter (case-insensitive exact match) ---
    # We use str.to_lowercase() on both sides for case-insensitive comparison.
    # Note: We use == for exact match (not contains) because positions are short
    # codes like "RF", "DH" — partial matching would cause false positives.
    if position is not None:
        df = df.filter(
            pl.col("position").str.to_lowercase() == position.lower()
        )

    # -----------------------------------------------------------------------
    # COMPUTED STAT: OBP (On-Base Percentage)
    # -----------------------------------------------------------------------
    # OBP doesn't exist in the database — it's derived from raw columns:
    #   OBP = (Hits + BB + HBP) / (AB + BB + HBP + SF)
    #
    # We compute it as a new column BEFORE the filtering step so that
    # min_obp / max_obp query params work just like any raw column filter.
    #
    # We also KEEP it in the results so the PlayerTable can display OBP
    # directly without needing a separate merge with /players/computed.
    #
    # Hits = batting_average * at_bats (since hits aren't stored directly).
    # .fill_null(0) handles players whose walks/HBP/SF data hasn't been
    # populated yet (older data before those columns were added).
    df = df.with_columns(
        pl.when(
            (pl.col("at_bats").fill_null(0) + pl.col("walks").fill_null(0)
             + pl.col("hit_by_pitch").fill_null(0) + pl.col("sacrifice_flies").fill_null(0)) > 0
        )
        .then(
            (
                (pl.col("batting_average") * pl.col("at_bats").fill_null(0)).round(0)
                + pl.col("walks").fill_null(0)
                + pl.col("hit_by_pitch").fill_null(0)
            ).cast(pl.Float64)
            / (
                pl.col("at_bats").fill_null(0)
                + pl.col("walks").fill_null(0)
                + pl.col("hit_by_pitch").fill_null(0)
                + pl.col("sacrifice_flies").fill_null(0)
            ).cast(pl.Float64)
        )
        .otherwise(None)
        .round(3)
        .alias("obp")
    )

    # --- Stat range filters (dynamic) ---
    # Instead of hardcoding each stat, we loop over all numeric columns
    # and check the query string for min_<stat> and max_<stat> params.
    # This means adding a new stat column to models.py automatically
    # makes it filterable — no endpoint code changes needed.
    numeric_cols = get_numeric_stat_columns()
    query_params = dict(request.query_params)

    # Combine raw database columns with computed stat columns (OBP).
    # This lets the same filtering loop handle both types seamlessly.
    computed_stat_cols = ["obp"]
    all_filterable_cols = numeric_cols + computed_stat_cols

    for stat in all_filterable_cols:
        # Check if this column exists in the DataFrame
        if stat not in df.columns:
            continue

        col_is_float = df.schema[stat] in (pl.Float64, pl.Float32)

        min_key = f"min_{stat}"
        if min_key in query_params:
            min_val = float(query_params[min_key]) if col_is_float else int(query_params[min_key])
            df = df.filter(pl.col(stat) >= min_val)

        max_key = f"max_{stat}"
        if max_key in query_params:
            max_val = float(query_params[max_key]) if col_is_float else int(query_params[max_key])
            df = df.filter(pl.col(stat) <= max_val)

    # NOTE: We intentionally KEEP the computed OBP column in the results.
    # This way the PlayerTable can display it directly from search results
    # without needing a separate merge with /players/computed.
    results = df.to_dicts()

    # Build a descriptive message showing what filters were applied
    filter_parts = []
    if team:
        filter_parts.append(f"team='{team}'")
    if position:
        filter_parts.append(f"position='{position}'")
    for stat in all_filterable_cols:
        min_key = f"min_{stat}"
        max_key = f"max_{stat}"
        if min_key in query_params:
            filter_parts.append(f"{min_key}={query_params[min_key]}")
        if max_key in query_params:
            filter_parts.append(f"{max_key}={query_params[max_key]}")

    filter_desc = ", ".join(filter_parts) if filter_parts else "none"
    count = len(results)

    return ApiResponse(
        code=200,
        message=f"Found {count} player(s) matching filters: {filter_desc}",
        data={"results": results, "count": count}
    )


@app.get("/players/stats")
async def get_aggregated_stats():
    """
    Compute league-wide average statistics using Polars.

    This endpoint demonstrates several Polars concepts:
    - Creating a DataFrame from database query results
    - Selecting specific columns with pl.col()
    - Aggregation with .mean()
    - Aliasing computed columns with .alias()
    - Converting a DataFrame back to a Python dict with .to_dict()

    The Polars expression `pl.col("batting_average").mean().alias("avg_batting_average")`
    is equivalent to SQL: SELECT AVG(batting_average) AS avg_batting_average FROM players

    Returns:
        A JSON object with averaged stats. Format: {"avg_batting_average": [0.297], ...}
        Note: Values are arrays because Polars' to_dict(as_series=False) wraps
        each column's values in a list — even single values.
    """
    query = players.select()
    rows = await database.fetch_all(query)

    # Create a Polars DataFrame from the database rows.
    # Polars can accept a list of dict-like objects (which database Row objects are).
    df = rows_to_dataframe(rows)

    if df.is_empty():
        return {"detail": "No player data available."}

    # Filter to only players with 200+ at bats for meaningful averages.
    # Players with few at bats can skew league averages significantly.
    if "at_bats" in df.columns:
        qualified = df.filter(pl.col("at_bats").is_not_null() & (pl.col("at_bats") >= 200))
        if qualified.is_empty():
            qualified = df  # Fall back to all players if none qualify
    else:
        qualified = df

    # Dynamically get all numeric stat columns from the table definition.
    # This means if we add new stat columns later, they'll automatically
    # be included in the averages without changing this code.
    numeric_cols = get_numeric_stat_columns()

    # Build a list of Polars expressions using a list comprehension.
    # Each expression: select a column -> compute its mean -> rename it with "avg_" prefix.
    # pl.col("home_runs").mean().alias("avg_home_runs") produces the average of home_runs.
    avg_stats = qualified.select(
        [pl.col(col).mean().alias(f"avg_{col}") for col in numeric_cols]
    )

    # .to_dict(as_series=False) converts the DataFrame to a plain Python dict.
    # as_series=False means values are plain lists, not Polars Series objects.
    # Result looks like: {"avg_batting_average": [0.2967], "avg_home_runs": [38.9], ...}
    return avg_stats.to_dict(as_series=False)


@app.get("/players/computed")
async def get_computed_stats():
    """
    Compute per-player derived statistics using Polars expressions.

    This endpoint showcases more advanced Polars features:
    - .with_columns(): Add new computed columns to an existing DataFrame
    - Column arithmetic: Multiply, divide, add columns together
    - .round(): Round float values to a specific number of decimal places
    - .select(): Choose which columns to include in the output

    Computed stats:
    - OBP: On-Base Percentage — (H + BB + HBP) / (AB + BB + HBP + SF).
      Measures how often a batter reaches base. Average: .320, Elite: .400+.
    - Power Index: home_runs * ops — measures raw power output.
      Higher HR count combined with high OPS indicates elite power.
    - Speed Score: stolen_bases / (stolen_bases + 10) * 100 — a normalized
      speed metric. The "+10" prevents division by zero and creates a curve
      where diminishing returns kick in at high SB counts.

    Returns:
        A JSON array of objects, each with player name, team, and computed stats.
    """
    query = players.select()
    rows = await database.fetch_all(query)
    df = rows_to_dataframe(rows)

    if df.is_empty():
        return {"detail": "No player data available."}

    # .with_columns() adds new columns to the DataFrame without removing existing ones.
    # Each argument is a Polars expression that defines a new column.
    computed = df.with_columns([
        # OBP (On-Base Percentage): Measures how frequently a batter reaches base.
        # Formula: (Hits + Walks + Hit-By-Pitch) / (At Bats + Walks + HBP + Sacrifice Flies)
        #
        # This is one of the most important offensive stats in modern baseball.
        # Unlike batting average, OBP credits walks and HBPs — a batter who
        # draws lots of walks is still getting on base even without a hit.
        #
        # We use pl.when() to guard against division by zero for players who
        # might have null or zero values in the denominator columns.
        # .fill_null(0) replaces any null values with 0 before the calculation,
        # since older data might not have walks/HBP/SF populated yet.
        pl.when(
            (pl.col("at_bats").fill_null(0) + pl.col("walks").fill_null(0)
             + pl.col("hit_by_pitch").fill_null(0) + pl.col("sacrifice_flies").fill_null(0)) > 0
        )
        .then(
            (
                (pl.col("batting_average") * pl.col("at_bats").fill_null(0)).round(0)  # Hits = AVG * AB
                + pl.col("walks").fill_null(0)
                + pl.col("hit_by_pitch").fill_null(0)
            ).cast(pl.Float64)
            / (
                pl.col("at_bats").fill_null(0)
                + pl.col("walks").fill_null(0)
                + pl.col("hit_by_pitch").fill_null(0)
                + pl.col("sacrifice_flies").fill_null(0)
            ).cast(pl.Float64)
        )
        .otherwise(None)
        .round(3)
        .alias("obp"),

        # Power Index: Multiply home_runs by OPS.
        # pl.col("home_runs") selects the column, then * pl.col("ops") does element-wise multiplication.
        # .round(2) rounds to 2 decimal places. .alias("power_index") names the new column.
        (pl.col("home_runs") * pl.col("ops")).round(2).alias("power_index"),

        # Speed Score: A normalized metric using the formula: SB / (SB + 10) * 100.
        # The denominator (SB + 10) ensures we never divide by zero and creates
        # a logarithmic-style curve — going from 0 to 10 SB has more impact
        # than going from 50 to 60 SB. Max possible score approaches 100.
        (pl.col("stolen_bases") / (pl.col("stolen_bases") + 10) * 100).round(1).alias("speed_score"),
    ])

    # .select() picks only the columns we want to return to the frontend.
    # Without this, we'd return ALL columns including the raw stats (which the
    # /players/ endpoint already provides).
    result = computed.select([
        "id", "name", "team",
        "obp", "power_index", "speed_score"
    ])

    # .to_dicts() converts the DataFrame to a list of dictionaries.
    # Unlike .to_dict(as_series=False) which gives {col: [values]},
    # .to_dicts() gives [{col: value}, {col: value}] — one dict per row.
    # This format matches what the frontend expects for mapping over players.
    return result.to_dicts()


@app.get("/players/team-stats")
async def get_team_stats():
    """
    Compute team-level aggregated statistics using Polars group_by.

    This endpoint demonstrates Polars' powerful group_by() operation:
    - .group_by("team"): Groups rows by the team column (like SQL GROUP BY)
    - .agg(): Defines what aggregations to compute for each group
    - .sort(): Orders the results by a column

    Each team gets averaged stats across all its players.
    This is similar to SQL: SELECT team, AVG(batting_average), ... GROUP BY team

    Returns:
        A JSON array of team stat objects, sorted by team OPS (best first).
    """
    query = players.select()
    rows = await database.fetch_all(query)
    df = rows_to_dataframe(rows)

    if df.is_empty():
        return {"detail": "No player data available."}

    # .group_by("team") groups all rows that share the same team value.
    # .agg() then computes aggregations within each group.
    # This is one of Polars' most powerful features for data analysis.
    team_stats = df.group_by("team").agg([
        # Count how many players are on each team
        pl.col("name").count().alias("player_count"),

        # Average each numeric stat across the team's players
        pl.col("batting_average").mean().round(3).alias("avg_batting_average"),
        pl.col("home_runs").mean().round(1).alias("avg_home_runs"),
        pl.col("rbi").mean().round(1).alias("avg_rbi"),
        pl.col("stolen_bases").mean().round(1).alias("avg_stolen_bases"),
        pl.col("ops").mean().round(3).alias("avg_ops"),
    ]).sort("avg_ops", descending=True)  # Sort by OPS, best teams first

    return team_stats.to_dicts()


@app.get("/players/filterable-stats")
async def get_filterable_stats():
    """
    Return metadata about which stats can be filtered, with their min/max ranges.

    This endpoint is consumed by the frontend to DYNAMICALLY build search filters.
    Instead of hardcoding stat names in the React components, the frontend calls
    this endpoint on load and generates filter inputs for each stat automatically.

    This means when you add a new stat column to models.py (e.g., "walks" or "era"),
    the search UI will automatically include it — no frontend changes needed.

    In addition to raw database columns, this includes COMPUTED stats like OBP
    (On-Base Percentage) that are derived from raw columns using Polars.

    Polars concepts demonstrated:
    - .select() with .min() and .max() to compute range boundaries
    - Dynamic column introspection via get_numeric_stat_columns()
    - pl.when().then().otherwise() for safe division with null handling

    Returns:
        ApiResponse with a list of stat metadata objects:
        [{"name": "batting_average", "type": "float", "min": 0.258, "max": 0.331}, ...]
    """
    query = players.select()
    rows = await database.fetch_all(query)
    df = rows_to_dataframe(rows)

    if df.is_empty():
        return ApiResponse(code=200, message="No player data", data=[])

    numeric_cols = get_numeric_stat_columns()
    stat_info = []

    for col in numeric_cols:
        # Get the actual min, max, and average values from the data.
        # min/max help the frontend set reasonable input ranges.
        # avg lets the frontend compute a useful default minimum threshold
        # (half the average) so users start with meaningful filter values.
        col_min = df.select(pl.col(col).min()).item()
        col_max = df.select(pl.col(col).max()).item()
        col_avg = df.select(pl.col(col).mean()).item()

        # Determine if the stat is integer or float based on the column dtype.
        # Polars uses Int64 for integers and Float64 for floats.
        col_type = "int" if df.schema[col] in (pl.Int64, pl.Int32) else "float"

        stat_info.append({
            "name": col,
            "type": col_type,
            "min": col_min,
            "max": col_max,
            "avg": round(col_avg, 3) if col_avg is not None else col_min,
        })

    # -----------------------------------------------------------------------
    # COMPUTED STAT: OBP (On-Base Percentage)
    # -----------------------------------------------------------------------
    # OBP isn't stored in the database — it's derived from raw columns:
    #   OBP = (Hits + BB + HBP) / (AB + BB + HBP + SF)
    #
    # We compute Hits as batting_average * at_bats since the players table
    # stores batting average rather than a raw hit count.
    #
    # We filter to players with a valid plate appearance denominator (AB + BB + HBP + SF > 0)
    # to avoid division-by-zero and to exclude players missing these fields.
    df_for_obp = df.filter(
        (pl.col("at_bats").fill_null(0) + pl.col("walks").fill_null(0)
         + pl.col("hit_by_pitch").fill_null(0) + pl.col("sacrifice_flies").fill_null(0)) > 0
    )
    if not df_for_obp.is_empty():
        obp_expr = (
            ((pl.col("batting_average") * pl.col("at_bats").fill_null(0)).round(0)
             + pl.col("walks").fill_null(0)
             + pl.col("hit_by_pitch").fill_null(0)).cast(pl.Float64)
            / (pl.col("at_bats").fill_null(0) + pl.col("walks").fill_null(0)
               + pl.col("hit_by_pitch").fill_null(0)
               + pl.col("sacrifice_flies").fill_null(0)).cast(pl.Float64)
        )
        obp_min = df_for_obp.select(obp_expr.min()).item()
        obp_max = df_for_obp.select(obp_expr.max()).item()
        obp_avg = df_for_obp.select(obp_expr.mean()).item()

        stat_info.append({
            "name": "obp",
            "type": "float",
            "min": round(obp_min, 3),
            "max": round(obp_max, 3),
            "avg": round(obp_avg, 3) if obp_avg is not None else round(obp_min, 3),
        })

    # Also gather the list of unique positions and teams for dropdown filters
    positions = sorted([p for p in df["position"].unique().to_list() if p is not None])
    teams = sorted(df["team"].unique().to_list())

    # Total filterable stats = raw database columns + computed stats (OBP)
    return ApiResponse(
        code=200,
        message=f"Found {len(stat_info)} filterable stats",
        data={
            "stats": stat_info,
            "positions": positions,
            "teams": teams,
        }
    )


# ===========================================================================
# PITCHER ENDPOINTS
# ===========================================================================

@app.get("/pitchers/", response_model=list[PitcherOut])
async def get_pitchers():
    """
    Retrieve all pitchers from the database.

    Returns:
        A JSON array of all pitchers with their stats.
    """
    query = pitchers.select()
    results = await database.fetch_all(query)
    return results


@app.post("/pitchers/", response_model=ApiResponse)
async def create_pitcher(pitcher: PitcherIn):
    """
    Create a new pitcher in the database.

    Args:
        pitcher: A PitcherIn Pydantic model parsed from the JSON request body.

    Returns:
        ApiResponse with code 201 and the created pitcher data including their ID.
    """
    query = pitchers.insert().values(**pitcher.dict())
    pitcher_id = await database.execute(query)

    return ApiResponse(
        code=201,
        message=f"Pitcher '{pitcher.name}' created successfully",
        data={**pitcher.dict(), "id": pitcher_id}
    )


@app.put("/pitchers/{pitcher_id}", response_model=ApiResponse)
async def update_pitcher(pitcher_id: int, pitcher: PitcherUpdate):
    """
    Update an existing pitcher's data.

    Args:
        pitcher_id: The pitcher's database ID from the URL path.
        pitcher: A PitcherUpdate Pydantic model with the fields to change.

    Returns:
        ApiResponse with code 200 and the updated pitcher data, or 404 if not found.
    """
    update_data = pitcher.dict(exclude_unset=True)

    if not update_data:
        return ApiResponse(code=400, message="No fields provided to update", data=None)

    existing = await database.fetch_one(
        pitchers.select().where(pitchers.c.id == pitcher_id)
    )
    if not existing:
        return ApiResponse(code=404, message=f"Pitcher with id {pitcher_id} not found", data=None)

    query = pitchers.update().where(pitchers.c.id == pitcher_id).values(**update_data)
    await database.execute(query)

    updated = await database.fetch_one(
        pitchers.select().where(pitchers.c.id == pitcher_id)
    )

    return ApiResponse(
        code=200,
        message=f"Pitcher '{updated._mapping['name']}' updated successfully",
        data=dict(updated._mapping)
    )


@app.get("/pitchers/stats")
async def get_pitcher_aggregated_stats():
    """
    Compute league-wide average pitching statistics using Polars.

    Returns:
        A JSON object with averaged pitcher stats.
    """
    query = pitchers.select()
    rows = await database.fetch_all(query)
    df = rows_to_dataframe(rows)

    if df.is_empty():
        return {"detail": "No pitcher data available."}

    # Compute averages for key pitching stats
    avg_stats = df.select([
        pl.col("era").mean().round(2).alias("avg_era"),
        pl.col("whip").mean().round(2).alias("avg_whip"),
        pl.col("wins").mean().round(1).alias("avg_wins"),
        pl.col("losses").mean().round(1).alias("avg_losses"),
        pl.col("innings_pitched").mean().round(1).alias("avg_innings_pitched"),
        pl.col("strikeouts").mean().round(1).alias("avg_strikeouts"),
        pl.col("walks").mean().round(1).alias("avg_walks"),
    ])

    return avg_stats.to_dict(as_series=False)


@app.get("/pitchers/computed")
async def get_pitcher_computed_stats():
    """
    Compute per-pitcher derived statistics using Polars expressions.

    Computed stats:
    - K/9: Strikeouts per 9 innings pitched
    - BB/9: Walks per 9 innings pitched
    - K/BB: Strikeout to walk ratio
    - WHIP: Already in the data, but we include it for completeness

    Returns:
        A JSON array of objects with pitcher name, team, and computed stats.
    """
    query = pitchers.select()
    rows = await database.fetch_all(query)
    df = rows_to_dataframe(rows)

    if df.is_empty():
        return {"detail": "No pitcher data available."}

    # Compute advanced pitching metrics.
    # These derived stats give deeper insight into pitcher performance
    # beyond the raw counting stats stored in the database.
    computed = df.with_columns([
        # K/9: Strikeouts per 9 innings — measures strikeout ability.
        # Higher is better. Elite pitchers average 10+ K/9.
        ((pl.col("strikeouts") / pl.col("innings_pitched")) * 9).round(2).alias("k_per_9"),

        # BB/9: Walks per 9 innings — measures control.
        # Lower is better. Elite pitchers keep this under 2.0.
        ((pl.col("walks") / pl.col("innings_pitched")) * 9).round(2).alias("bb_per_9"),

        # K/BB: Strikeout to walk ratio (higher is better).
        # This measures command — can the pitcher get Ks without walking batters?
        (pl.col("strikeouts") / pl.col("walks")).round(2).alias("k_bb_ratio"),

        # Win percentage: Wins / (Wins + Losses) × 100
        (pl.col("wins") / (pl.col("wins") + pl.col("losses")) * 100).round(1).alias("win_pct"),

        # HR/9: Home runs allowed per 9 innings — measures vulnerability to the long ball.
        # Lower is better. League average ~1.2, elite <0.7.
        # Key fantasy stat because home runs are the most damaging hit type.
        ((pl.col("home_runs_allowed") / pl.col("innings_pitched")) * 9).round(2).alias("hr_per_9"),
    ])

    result = computed.select([
        "id", "name", "team",
        "k_per_9", "bb_per_9", "k_bb_ratio", "win_pct", "hr_per_9"
    ])

    return result.to_dicts()


@app.get("/pitchers/team-stats")
async def get_pitcher_team_stats():
    """
    Compute team-level aggregated pitching statistics using Polars group_by.

    Returns:
        A JSON array of team pitching stat objects, sorted by team ERA (best first).
    """
    query = pitchers.select()
    rows = await database.fetch_all(query)
    df = rows_to_dataframe(rows)

    if df.is_empty():
        return {"detail": "No pitcher data available."}

    team_stats = df.group_by("team").agg([
        pl.col("name").count().alias("pitcher_count"),
        pl.col("era").mean().round(2).alias("avg_era"),
        pl.col("whip").mean().round(2).alias("avg_whip"),
        pl.col("wins").sum().alias("total_wins"),
        pl.col("losses").sum().alias("total_losses"),
        pl.col("strikeouts").sum().alias("total_strikeouts"),
        pl.col("innings_pitched").sum().round(1).alias("total_innings"),
    ]).sort("avg_era", descending=False)  # Sort by ERA, best teams first

    return team_stats.to_dicts()


# ===========================================================================
# HELPER: Get Numeric Pitcher Stat Columns
# ===========================================================================

def get_numeric_pitcher_stat_columns():
    """
    Return the list of numeric stat column names from the pitchers table.

    Same pattern as get_numeric_stat_columns() but for the pitchers table.
    Dynamically inspects the SQLAlchemy table definition to find all
    Float and Integer columns (excluding 'id' and 'mlb_id' which aren't stats).

    Returns:
        list[str]: Column names like ["wins", "losses", "era", "whip", ...]
    """
    from sqlalchemy import Float, Integer
    return [
        col.name for col in pitchers.columns
        if isinstance(col.type, (Float, Integer)) and col.name not in ("id", "mlb_id")
    ]


@app.get("/pitchers/filterable-stats")
async def get_pitcher_filterable_stats():
    """
    Return metadata about which pitcher stats can be filtered, with their min/max ranges.

    This endpoint mirrors /players/filterable-stats but for the pitchers table.
    The frontend uses this to dynamically build search filter inputs — adding
    a new stat column to the pitchers table in models.py automatically creates
    a new filter row in the UI.

    In addition to raw database columns, this endpoint also includes COMPUTED
    stats that are derived from raw columns using Polars expressions:
      - K/9:  (strikeouts / innings_pitched) * 9
      - BB/9: (walks / innings_pitched) * 9
      - K/BB: strikeouts / walks
      - HR/9: (home_runs_allowed / innings_pitched) * 9
    These don't live in the database — they're calculated on the fly.

    Returns:
        ApiResponse with a list of stat metadata objects and available positions/teams.
    """
    query = pitchers.select()
    rows = await database.fetch_all(query)
    df = rows_to_dataframe(rows)

    if df.is_empty():
        return ApiResponse(code=200, message="No pitcher data", data=[])

    numeric_cols = get_numeric_pitcher_stat_columns()
    stat_info = []

    for col in numeric_cols:
        # Get actual min/max/avg from the data for placeholder values in the UI.
        # avg is used by the frontend to compute a default min threshold
        # (half the average) so users start with meaningful filter values.
        col_min = df.select(pl.col(col).min()).item()
        col_max = df.select(pl.col(col).max()).item()
        col_avg = df.select(pl.col(col).mean()).item()

        col_type = "int" if df.schema[col] in (pl.Int64, pl.Int32) else "float"

        stat_info.append({
            "name": col,
            "type": col_type,
            "min": col_min,
            "max": col_max,
            "avg": round(col_avg, 3) if col_avg is not None else col_min,
        })

    # -----------------------------------------------------------------------
    # COMPUTED STATS: K/9, BB/9, K/BB, HR/9
    # -----------------------------------------------------------------------
    # These stats aren't stored in the database — they're derived from raw
    # columns using Polars. We compute their min/max ranges here so the
    # frontend can display them as filter inputs with realistic placeholders.
    #
    # We filter to pitchers with innings_pitched > 0 (for K/9, BB/9, HR/9)
    # or walks > 0 (for K/BB) to avoid division-by-zero errors.
    # A pitcher with 0 IP or 0 BB has no meaningful rate stat.

    # --- K/9 and BB/9 and HR/9 all need innings_pitched > 0 ---
    df_with_ip = df.filter(pl.col("innings_pitched") > 0)
    if not df_with_ip.is_empty():
        # K/9: Strikeouts per 9 innings — (strikeouts / IP) * 9
        k_per_9_expr = (pl.col("strikeouts") / pl.col("innings_pitched")) * 9
        k9_min = df_with_ip.select(k_per_9_expr.min()).item()
        k9_max = df_with_ip.select(k_per_9_expr.max()).item()
        k9_avg = df_with_ip.select(k_per_9_expr.mean()).item()
        stat_info.append({
            "name": "k_per_9",
            "type": "float",
            "min": round(k9_min, 2),
            "max": round(k9_max, 2),
            "avg": round(k9_avg, 2) if k9_avg is not None else round(k9_min, 2),
        })

        # BB/9: Walks per 9 innings — (walks / IP) * 9
        bb_per_9_expr = (pl.col("walks") / pl.col("innings_pitched")) * 9
        bb9_min = df_with_ip.select(bb_per_9_expr.min()).item()
        bb9_max = df_with_ip.select(bb_per_9_expr.max()).item()
        bb9_avg = df_with_ip.select(bb_per_9_expr.mean()).item()
        stat_info.append({
            "name": "bb_per_9",
            "type": "float",
            "min": round(bb9_min, 2),
            "max": round(bb9_max, 2),
            "avg": round(bb9_avg, 2) if bb9_avg is not None else round(bb9_min, 2),
        })

        # HR/9: Home runs allowed per 9 innings — (HR / IP) * 9
        hr_per_9_expr = (pl.col("home_runs_allowed") / pl.col("innings_pitched")) * 9
        hr9_min = df_with_ip.select(hr_per_9_expr.min()).item()
        hr9_max = df_with_ip.select(hr_per_9_expr.max()).item()
        hr9_avg = df_with_ip.select(hr_per_9_expr.mean()).item()
        stat_info.append({
            "name": "hr_per_9",
            "type": "float",
            "min": round(hr9_min, 2),
            "max": round(hr9_max, 2),
            "avg": round(hr9_avg, 2) if hr9_avg is not None else round(hr9_min, 2),
        })

    # --- K/BB needs walks > 0 to avoid division by zero ---
    df_with_bb = df.filter(pl.col("walks") > 0)
    if not df_with_bb.is_empty():
        # K/BB: Strikeout-to-walk ratio — strikeouts / walks
        k_bb_expr = pl.col("strikeouts") / pl.col("walks")
        kbb_min = df_with_bb.select(k_bb_expr.min()).item()
        kbb_max = df_with_bb.select(k_bb_expr.max()).item()
        kbb_avg = df_with_bb.select(k_bb_expr.mean()).item()
        stat_info.append({
            "name": "k_bb_ratio",
            "type": "float",
            "min": round(kbb_min, 2),
            "max": round(kbb_max, 2),
            "avg": round(kbb_avg, 2) if kbb_avg is not None else round(kbb_min, 2),
        })

    # Gather unique positions (SP, RP) and teams for dropdown filters
    positions = sorted([p for p in df["position"].unique().to_list() if p is not None])
    teams = sorted(df["team"].unique().to_list())

    # Total filterable stats = raw database columns + computed stats
    return ApiResponse(
        code=200,
        message=f"Found {len(stat_info)} filterable pitcher stats",
        data={
            "stats": stat_info,
            "positions": positions,
            "teams": teams,
        }
    )


@app.get("/pitchers/search", response_model=ApiResponse)
async def search_pitchers(
    request: Request,
    team: Optional[str] = Query(None, description="Filter by team name (case-insensitive partial match)"),
    position: Optional[str] = Query(None, description="Filter by position (SP or RP)"),
):
    """
    Search and filter pitchers using Polars DataFrame operations.

    Mirrors the /players/search endpoint but for the pitchers table.
    Supports dynamic stat range filtering via min_<stat> and max_<stat>
    query parameters, just like the batter search.

    In addition to raw database columns, this endpoint also supports filtering
    by COMPUTED stats like K/9 (strikeouts per 9 innings). These are derived
    columns calculated on the fly using Polars — they don't exist in the
    database but are computed from raw columns before filtering is applied.

    Examples:
    - /pitchers/search?position=SP&max_era=3.5
    - /pitchers/search?team=yankees&min_strikeouts=150
    - /pitchers/search?min_wins=10&max_whip=1.1
    - /pitchers/search?min_k_per_9=9.0  (computed stat filter)

    Returns:
        ApiResponse with code 200 and a "results" list of matching pitchers,
        plus a "count" showing how many matched.
    """
    query = pitchers.select()
    rows = await database.fetch_all(query)
    df = rows_to_dataframe(rows)

    if df.is_empty():
        return ApiResponse(code=200, message="No pitchers in database", data={"results": [], "count": 0})

    # --- Team filter (case-insensitive partial match) ---
    if team is not None:
        df = df.filter(
            pl.col("team").str.to_lowercase().str.contains(team.lower())
        )

    # --- Position filter (case-insensitive exact match) ---
    if position is not None:
        df = df.filter(
            pl.col("position").str.to_lowercase() == position.lower()
        )

    # -----------------------------------------------------------------------
    # COMPUTED STATS: K/9, BB/9, K/BB, HR/9
    # -----------------------------------------------------------------------
    # These four stats don't exist in the database — they're derived from
    # raw columns using Polars expressions. We compute them as new columns
    # on the DataFrame BEFORE the filtering step. This serves two purposes:
    #
    # 1. FILTERING: Allows min_k_per_9, max_bb_per_9, etc. query params to
    #    work just like any raw database column filter.
    #
    # 2. DISPLAY: Includes the computed values in the search results so the
    #    PitcherTable can display them directly (K/9, BB/9, K/BB, HR/9)
    #    instead of showing "—" dashes for missing data. Without this, the
    #    frontend would need a separate merge step with /pitchers/computed.
    #
    # All formulas use pl.when() to guard against division-by-zero:
    # pitchers with 0 innings_pitched or 0 walks get null instead of Infinity.
    df = df.with_columns([
        # K/9: Strikeouts per 9 innings — measures strikeout ability.
        # Formula: (strikeouts / innings_pitched) * 9
        # Higher is better. Average: ~8.0, Elite: 11.0+
        pl.when(pl.col("innings_pitched") > 0)
          .then(((pl.col("strikeouts") / pl.col("innings_pitched")) * 9).round(2))
          .otherwise(None)
          .alias("k_per_9"),

        # BB/9: Walks per 9 innings — measures control/command.
        # Formula: (walks / innings_pitched) * 9
        # Lower is better. Average: ~3.0, Elite: <2.0
        pl.when(pl.col("innings_pitched") > 0)
          .then(((pl.col("walks") / pl.col("innings_pitched")) * 9).round(2))
          .otherwise(None)
          .alias("bb_per_9"),

        # K/BB: Strikeout-to-walk ratio — measures overall command.
        # Formula: strikeouts / walks
        # Higher is better. Average: ~2.5, Elite: 5.0+
        # Guards against division by zero when walks = 0.
        pl.when(pl.col("walks") > 0)
          .then((pl.col("strikeouts") / pl.col("walks")).round(2))
          .otherwise(None)
          .alias("k_bb_ratio"),

        # HR/9: Home runs allowed per 9 innings — measures homer vulnerability.
        # Formula: (home_runs_allowed / innings_pitched) * 9
        # Lower is better. Average: ~1.2, Elite: <0.7
        pl.when(pl.col("innings_pitched") > 0)
          .then(((pl.col("home_runs_allowed") / pl.col("innings_pitched")) * 9).round(2))
          .otherwise(None)
          .alias("hr_per_9"),
    ])

    # --- Dynamic stat range filters ---
    # Loop over all numeric pitcher columns and check for min_*/max_* query params.
    # This mirrors the batter search pattern — adding a new stat column to
    # models.py automatically makes it filterable here.
    numeric_cols = get_numeric_pitcher_stat_columns()
    query_params = dict(request.query_params)

    # Combine raw database columns with computed stat columns.
    # This lets the same filtering loop handle both types seamlessly —
    # the loop doesn't need to know whether a column is raw or computed.
    computed_stat_cols = ["k_per_9", "bb_per_9", "k_bb_ratio", "hr_per_9"]
    all_filterable_cols = numeric_cols + computed_stat_cols

    for stat in all_filterable_cols:
        # Check if this column exists in the DataFrame (might be all null for new columns)
        if stat not in df.columns:
            continue

        # Determine if the column holds float values (affects type casting).
        # Computed stats like k_per_9 are always float.
        col_is_float = df.schema[stat] in (pl.Float64, pl.Float32)

        min_key = f"min_{stat}"
        if min_key in query_params:
            min_val = float(query_params[min_key]) if col_is_float else int(query_params[min_key])
            df = df.filter(pl.col(stat) >= min_val)

        max_key = f"max_{stat}"
        if max_key in query_params:
            max_val = float(query_params[max_key]) if col_is_float else int(query_params[max_key])
            df = df.filter(pl.col(stat) <= max_val)

    # NOTE: We intentionally KEEP the computed columns (k_per_9, bb_per_9,
    # k_bb_ratio, hr_per_9) in the results. This way the PitcherTable can
    # display them directly from the search results without needing a
    # separate merge step with /pitchers/computed. The table columns marked
    # isComputed: true will find these values on the pitcher object itself.
    results = df.to_dicts()

    # Build a descriptive message showing what filters were applied
    filter_parts = []
    if team:
        filter_parts.append(f"team='{team}'")
    if position:
        filter_parts.append(f"position='{position}'")
    for stat in all_filterable_cols:
        min_key = f"min_{stat}"
        max_key = f"max_{stat}"
        if min_key in query_params:
            filter_parts.append(f"{min_key}={query_params[min_key]}")
        if max_key in query_params:
            filter_parts.append(f"{max_key}={query_params[max_key]}")

    filter_desc = ", ".join(filter_parts) if filter_parts else "none"
    count = len(results)

    return ApiResponse(
        code=200,
        message=f"Found {count} pitcher(s) matching filters: {filter_desc}",
        data={"results": results, "count": count}
    )


# ===========================================================================
# ROLLING STATS ENDPOINTS (Time-Period Averages)
# ===========================================================================
# These endpoints power the "Last 5 / 10 / 15 / 30 days" feature.
#
# How rolling stats work:
# 1. Frontend sends a `days` parameter (e.g., 15)
# 2. Backend computes the cutoff date: today - 15 days
# 3. Filter game logs to only games on or after the cutoff date
# 4. Group by player and aggregate: sum hits, sum at-bats, etc.
# 5. Compute derived stats from the sums (e.g., avg = hits / at_bats)
# 6. Return one row per player with their rolling stats
#
# Why compute on the backend (not frontend)?
# - Game logs can be 15,000+ rows — too much data to send to the browser
# - Polars is much faster at aggregation than JavaScript
# - The API returns a clean, small result set (one row per player)
# ===========================================================================

@app.get("/players/rolling-stats")
async def get_batter_rolling_stats(
    days: int = Query(15, description="Number of days to look back (5, 10, 15, or 30)")
):
    """
    Compute batting stats over a rolling time window using game log data.

    This endpoint demonstrates advanced Polars aggregation:
    - Date string filtering using .filter() with string comparison
    - .group_by() with multiple .sum() and custom expressions
    - Computing derived stats (batting avg, OPS) from raw sums
    - .sort() for ordering results by a computed column

    The date filtering works because game_date is stored as "YYYY-MM-DD" strings,
    which sort lexicographically in the correct chronological order. So
    "2024-07-15" > "2024-07-01" is True, exactly like a date comparison.

    Rolling stats computed:
    - Batting Average: hits / at_bats (over the window)
    - OPS: OBP + SLG computed from game-level data
    - Counting stats: HR, RBI, SB, R, K summed over the window
    - Games: number of games played in the window

    Args:
        days: How many days back to look (default 15). Common values: 5, 10, 15, 30.

    Returns:
        JSON array of player objects with rolling stats, sorted by OPS descending.
    """
    # Fetch all batter game logs from the database
    rows = await database.fetch_all(batter_game_logs.select())
    df = rows_to_dataframe(rows)

    if df.is_empty():
        return []

    # Compute the cutoff date as a string in "YYYY-MM-DD" format.
    # datetime.now() gets the current date/time, timedelta(days=N) subtracts N days.
    # .strftime("%Y-%m-%d") formats the date as a string matching our game_date format.
    # Example: if today is 2024-09-15 and days=15, cutoff = "2024-09-01"
    cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    # Filter to only games within the time window.
    # String comparison works because ISO dates ("YYYY-MM-DD") sort correctly:
    # "2024-09-15" >= "2024-09-01" is True (game is within window)
    # "2024-08-01" >= "2024-09-01" is False (game is outside window)
    df = df.filter(pl.col("game_date") >= cutoff_date)

    if df.is_empty():
        return []

    # Group by player and aggregate all their games in the window.
    # .group_by("player_id") creates one group per unique player.
    # .agg([...]) defines what to compute for each group.
    #
    # For counting stats (HR, RBI, etc.), we use .sum() to get the total.
    # For rate stats (batting average, OPS), we need the raw sums first,
    # then compute the rate after aggregation.
    rolling = df.group_by("player_id").agg([
        # .first() takes the first value in the group — works for fields
        # that are the same across all rows for a player (name, team).
        pl.col("player_name").first().alias("name"),
        pl.col("team").first(),

        # Count how many games this player appeared in during the window.
        # .count() counts non-null values in the group.
        pl.col("game_date").count().alias("games"),

        # Sum the counting stats across all games in the window.
        # These raw sums are both the final output (for HR, RBI, etc.)
        # and inputs to rate stat calculations (hits/AB for batting avg).
        pl.col("at_bats").sum(),
        pl.col("hits").sum(),
        pl.col("doubles").sum(),
        pl.col("triples").sum(),
        pl.col("home_runs").sum(),
        pl.col("rbi").sum(),
        pl.col("runs").sum(),
        pl.col("stolen_bases").sum(),
        pl.col("walks").sum(),
        pl.col("strikeouts").sum(),
        pl.col("hit_by_pitch").sum(),
        pl.col("sacrifice_flies").sum(),
    ])

    # Now compute rate stats from the aggregated sums.
    # .with_columns() adds new columns to the DataFrame.
    #
    # Batting Average = Hits / At Bats
    # OBP (On-Base Percentage) = (H + BB + HBP) / (AB + BB + HBP + SF)
    # SLG (Slugging Percentage) = Total Bases / At Bats
    #   where Total Bases = 1B + (2 × 2B) + (3 × 3B) + (4 × HR)
    #   and 1B (singles) = H - 2B - 3B - HR
    # OPS = OBP + SLG
    rolling = rolling.with_columns([
        # Batting Average: hits divided by at-bats.
        # .when().then().otherwise() handles division by zero:
        # if at_bats == 0, return 0.0 instead of NaN or infinity.
        pl.when(pl.col("at_bats") > 0)
          .then(pl.col("hits") / pl.col("at_bats"))
          .otherwise(0.0)
          .round(3)
          .alias("batting_average"),

        # Total Bases for SLG calculation.
        # Singles = H - 2B - 3B - HR (hits minus extra-base hits)
        # TB = 1×1B + 2×2B + 3×3B + 4×HR
        (
            (pl.col("hits") - pl.col("doubles") - pl.col("triples") - pl.col("home_runs"))  # singles
            + (pl.col("doubles") * 2)
            + (pl.col("triples") * 3)
            + (pl.col("home_runs") * 4)
        ).alias("total_bases"),
    ])

    # Compute OPS (On-base Plus Slugging) — the gold standard rate stat.
    # We do this in a second .with_columns() because it depends on total_bases
    # which we just computed above. Polars processes .with_columns() in order,
    # but columns created in the same call can't reference each other.
    rolling = rolling.with_columns([
        # OBP = (H + BB + HBP) / (AB + BB + HBP + SF)
        # This is the "reached base" percentage.
        pl.when((pl.col("at_bats") + pl.col("walks") + pl.col("hit_by_pitch") + pl.col("sacrifice_flies")) > 0)
          .then(
              (pl.col("hits") + pl.col("walks") + pl.col("hit_by_pitch")).cast(pl.Float64)
              / (pl.col("at_bats") + pl.col("walks") + pl.col("hit_by_pitch") + pl.col("sacrifice_flies")).cast(pl.Float64)
          )
          .otherwise(0.0)
          .alias("obp"),

        # SLG = Total Bases / At Bats
        pl.when(pl.col("at_bats") > 0)
          .then(pl.col("total_bases").cast(pl.Float64) / pl.col("at_bats").cast(pl.Float64))
          .otherwise(0.0)
          .alias("slg"),
    ])

    # OPS = OBP + SLG (now both columns exist, we can add them)
    rolling = rolling.with_columns([
        (pl.col("obp") + pl.col("slg")).round(3).alias("ops"),
    ])

    # Select the columns to return, in a clean order.
    # Drop intermediate columns (slg, doubles, triples, etc.)
    # that the frontend doesn't need to display.
    # OBP is included so the PlayerTable can display it in rolling mode.
    result = rolling.select([
        "player_id", "name", "team", "games",
        "at_bats", "batting_average", "obp", "home_runs", "rbi",
        "runs", "stolen_bases", "strikeouts", "ops", "total_bases",
    ]).sort("ops", descending=True)  # Sort by OPS — best hitters first

    return result.to_dicts()


@app.get("/pitchers/rolling-stats")
async def get_pitcher_rolling_stats(
    days: int = Query(15, description="Number of days to look back (5, 10, 15, or 30)")
):
    """
    Compute pitching stats over a rolling time window using game log data.

    Same pattern as batter rolling stats but with pitcher-specific aggregations.

    Rolling stats computed:
    - ERA: (earned_runs / innings_pitched) × 9
    - WHIP: (walks + hits_allowed) / innings_pitched
    - K/9: (strikeouts / innings_pitched) × 9
    - Counting stats: W, L, SV, QS, K summed over the window
    - Games: number of appearances in the window

    Args:
        days: How many days back to look (default 15).

    Returns:
        JSON array of pitcher objects with rolling stats, sorted by ERA ascending (best first).
    """
    rows = await database.fetch_all(pitcher_game_logs.select())
    df = rows_to_dataframe(rows)

    if df.is_empty():
        return []

    cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    df = df.filter(pl.col("game_date") >= cutoff_date)

    if df.is_empty():
        return []

    # Group by pitcher and sum all their game stats within the window.
    rolling = df.group_by("player_id").agg([
        pl.col("player_name").first().alias("name"),
        pl.col("team").first(),

        # Count games (appearances) in the window
        pl.col("game_date").count().alias("games"),

        # Sum innings pitched — note: IP uses baseball notation (6.1 = 6 1/3)
        # but for our average calculations, the decimal form works fine.
        pl.col("innings_pitched").sum(),
        pl.col("hits_allowed").sum(),
        pl.col("earned_runs").sum(),
        pl.col("walks").sum(),
        pl.col("strikeouts").sum(),
        pl.col("home_runs_allowed").sum(),

        # Sum binary stats (each is 0 or 1 per game)
        pl.col("wins").sum(),
        pl.col("losses").sum(),
        pl.col("saves").sum(),
        pl.col("quality_start").sum().alias("quality_starts"),
        pl.col("pitches").sum(),
    ])

    # Compute rate stats from the summed totals.
    # ERA = (Earned Runs / Innings Pitched) × 9
    # WHIP = (Walks + Hits Allowed) / Innings Pitched
    # K/9 = (Strikeouts / Innings Pitched) × 9
    # HR/9 = (Home Runs Allowed / Innings Pitched) × 9
    rolling = rolling.with_columns([
        # ERA — the standard pitcher effectiveness metric.
        # Multiply by 9 because ERA is "earned runs per 9 innings".
        pl.when(pl.col("innings_pitched") > 0)
          .then((pl.col("earned_runs").cast(pl.Float64) / pl.col("innings_pitched")) * 9)
          .otherwise(0.0)
          .round(2)
          .alias("era"),

        # WHIP — walks + hits per inning pitched. Lower is better.
        pl.when(pl.col("innings_pitched") > 0)
          .then(
              (pl.col("walks").cast(pl.Float64) + pl.col("hits_allowed").cast(pl.Float64))
              / pl.col("innings_pitched")
          )
          .otherwise(0.0)
          .round(2)
          .alias("whip"),

        # K/9 — strikeouts per 9 innings. Higher is better (more Ks).
        pl.when(pl.col("innings_pitched") > 0)
          .then((pl.col("strikeouts").cast(pl.Float64) / pl.col("innings_pitched")) * 9)
          .otherwise(0.0)
          .round(2)
          .alias("k_per_9"),

        # HR/9 — home runs allowed per 9 innings. Lower is better.
        pl.when(pl.col("innings_pitched") > 0)
          .then((pl.col("home_runs_allowed").cast(pl.Float64) / pl.col("innings_pitched")) * 9)
          .otherwise(0.0)
          .round(2)
          .alias("hr_per_9"),
    ])

    # Round innings pitched for display
    rolling = rolling.with_columns([
        pl.col("innings_pitched").round(1),
    ])

    # Select columns for the response, sorted by ERA (best pitchers first).
    result = rolling.select([
        "player_id", "name", "team", "games",
        "innings_pitched", "era", "whip", "k_per_9", "hr_per_9",
        "wins", "losses", "saves", "quality_starts",
        "strikeouts", "walks", "earned_runs",
    ]).sort("era", descending=False)

    return result.to_dicts()


# ===========================================================================
# SAMPLE DATA (FALLBACK)
# ===========================================================================

async def populate_sample_data():
    """
    Insert sample MLB players into the database as a FALLBACK.

    This runs on startup ONLY if the players table is empty.
    In production, you should use mlb_data_fetcher.py to populate with real data:

        python mlb_data_fetcher.py --season 2024 --save

    This function provides fallback data so the app works even without
    running the fetcher first (useful for quick testing/demos).

    The stats below are from the 2024 season (abbreviated list).
    For the full 129+ qualified batters, run the mlb_data_fetcher.py script.

    database.execute_many() is an efficient way to insert multiple rows
    in a single operation — it's faster than calling execute() 10 times.
    """
    # Abbreviated list of top 2024 batters (for fallback only)
    # Full data should be loaded via: python mlb_data_fetcher.py --season 2024 --save
    sample_players = [
        {"name": "Aaron Judge", "team": "NY Yankees", "position": "CF", "batting_average": 0.322, "home_runs": 58, "rbi": 144, "stolen_bases": 10, "ops": 1.159},
        {"name": "Shohei Ohtani", "team": "LA Dodgers", "position": "DH", "batting_average": 0.310, "home_runs": 54, "rbi": 130, "stolen_bases": 59, "ops": 1.036},
        {"name": "Juan Soto", "team": "NY Yankees", "position": "RF", "batting_average": 0.288, "home_runs": 41, "rbi": 109, "stolen_bases": 7, "ops": 0.988},
        {"name": "Bobby Witt Jr.", "team": "Kansas City Royals", "position": "SS", "batting_average": 0.332, "home_runs": 32, "rbi": 109, "stolen_bases": 31, "ops": 0.977},
        {"name": "Marcell Ozuna", "team": "Atlanta Braves", "position": "DH", "batting_average": 0.302, "home_runs": 39, "rbi": 104, "stolen_bases": 1, "ops": 0.924},
        {"name": "José Ramírez", "team": "Cleveland Guardians", "position": "3B", "batting_average": 0.279, "home_runs": 39, "rbi": 118, "stolen_bases": 41, "ops": 0.872},
        {"name": "Gunnar Henderson", "team": "Baltimore Orioles", "position": "SS", "batting_average": 0.281, "home_runs": 37, "rbi": 92, "stolen_bases": 21, "ops": 0.893},
        {"name": "Yordan Alvarez", "team": "Houston Astros", "position": "DH", "batting_average": 0.308, "home_runs": 35, "rbi": 86, "stolen_bases": 6, "ops": 0.959},
        {"name": "Vladimir Guerrero Jr.", "team": "Toronto Blue Jays", "position": "1B", "batting_average": 0.323, "home_runs": 30, "rbi": 103, "stolen_bases": 2, "ops": 0.940},
        {"name": "Bryce Harper", "team": "Philadelphia Phillies", "position": "1B", "batting_average": 0.285, "home_runs": 30, "rbi": 87, "stolen_bases": 7, "ops": 0.898},
    ]
    await database.execute_many(query=players.insert(), values=sample_players)
