"""
models.py - Database Table Definitions (SQLAlchemy Core)
========================================================

This module defines the database tables using SQLAlchemy Core (NOT the ORM).

Key concepts:
- SQLAlchemy has two main styles: Core (lower-level, table-based) and ORM (classes).
  We use Core here because it's simpler and pairs well with the async `databases` library.
- Each Table() call registers a table with the `metadata` object from database.py.
- Column types (Integer, String, Float) map directly to PostgreSQL column types.

Tables:
- players: MLB batter statistics (batting_average, home_runs, rbi, etc.)
- pitchers: MLB pitcher statistics (era, whip, wins, losses, etc.)
- batter_game_logs: Per-game batting stats for computing rolling averages
- pitcher_game_logs: Per-game pitching stats for computing rolling averages
- fantasy_leagues: Fantasy league configurations and scoring rules (ESPN + Yahoo)
"""

from sqlalchemy import Table, Column, Integer, String, Float
from database import metadata  # Import the shared metadata registry

# =============================================================================
# PLAYERS TABLE (Batters)
# =============================================================================
# Stores MLB batter statistics. Primary key: auto-incrementing integer `id`.
# Position: the player's fielding position (e.g., "RF", "DH", "1B")
# Stats stored: batting_average, home_runs, rbi, stolen_bases, ops, runs, strikeouts, total_bases
# These raw stats are used by Polars (in main.py) to compute derived statistics
players = Table(
    "players",
    metadata,
    # primary_key=True makes this an auto-incrementing integer primary key.
    # PostgreSQL will generate IDs automatically when we insert rows.
    Column("id", Integer, primary_key=True),

    # String(100) maps to VARCHAR(100) in PostgreSQL.
    # The number is the maximum character length.
    Column("name", String(100)),
    Column("team", String(50)),

    # Position: the player's primary fielding position.
    # Common MLB positions: C, 1B, 2B, 3B, SS, LF, CF, RF, DH.
    # nullable=True allows existing rows (without position) to remain valid
    # after we add this column to an existing database.
    Column("position", String(10), nullable=True),

    # Float maps to DOUBLE PRECISION in PostgreSQL.
    # Used for decimal stats like batting average (e.g., 0.287).
    Column("batting_average", Float),

    # Integer maps to INTEGER in PostgreSQL.
    # Used for whole-number stats like home runs.
    Column("home_runs", Integer),
    Column("rbi", Integer),           # Runs Batted In
    Column("stolen_bases", Integer),

    # OPS = On-base Plus Slugging. A key offensive stat combining
    # on-base percentage and slugging percentage. Stored as a float (e.g., 1.019).
    Column("ops", Float),

    # Additional batting stats
    Column("runs", Integer, nullable=True),           # R - Runs scored
    Column("strikeouts", Integer, nullable=True),     # K - Strikeouts (batter)
    Column("total_bases", Integer, nullable=True),    # TB - Total bases
    Column("at_bats", Integer, nullable=True),        # AB - At bats
    Column("walks", Integer, nullable=True),          # BB - Bases on balls (walks)
    Column("hit_by_pitch", Integer, nullable=True),   # HBP - Hit by pitch
    Column("sacrifice_flies", Integer, nullable=True),# SF - Sacrifice flies

    # Fantasy-relevant stats — these are needed for accurate ESPN fantasy point
    # calculations. For example, a league that awards 1 pt per double needs the
    # actual doubles count, not just total bases.
    Column("hits", Integer, nullable=True),            # H - Total hits
    Column("doubles", Integer, nullable=True),         # 2B - Doubles
    Column("triples", Integer, nullable=True),         # 3B - Triples
    Column("caught_stealing", Integer, nullable=True), # CS - Caught stealing (usually negative pts)

    # Games played — number of games the player appeared in during the season.
    # Used to compute fantasy points per game (Pts/G).
    Column("games_played", Integer, nullable=True),   # G - Games played

    # MLB Stats API player ID — used to link this player to their game logs.
    # The MLB API assigns a unique numeric ID to every player (e.g., Aaron Judge = 592450).
    # We store this so we can match season totals to per-game data in batter_game_logs.
    Column("mlb_id", Integer, nullable=True),
)


# =============================================================================
# PITCHERS TABLE
# =============================================================================
# Stores MLB pitcher statistics. Primary key: auto-incrementing integer `id`.
# Stats stored: era, whip, wins, losses, saves, innings_pitched, hits_allowed,
#               earned_runs, walks, strikeouts, home_runs_allowed
pitchers = Table(
    "pitchers",
    metadata,
    Column("id", Integer, primary_key=True),

    Column("name", String(100)),
    Column("team", String(50)),

    # Position: SP (Starting Pitcher) or RP (Relief Pitcher)
    Column("position", String(10), nullable=True),

    # Win-Loss record
    Column("wins", Integer),
    Column("losses", Integer),

    # ERA = Earned Run Average. Earned runs allowed per 9 innings pitched.
    # Lower is better. League average ~4.00, elite <3.00.
    Column("era", Float),

    # WHIP = Walks + Hits per Inning Pitched.
    # Lower is better. League average ~1.30, elite <1.00.
    Column("whip", Float),

    # Games and innings
    Column("games", Integer, nullable=True),           # G - Games appeared
    Column("games_started", Integer, nullable=True),   # GS - Games started
    Column("innings_pitched", Float),                  # IP - Innings pitched (e.g., 6.2 = 6 2/3 innings)

    # Pitching stats
    Column("hits_allowed", Integer),                   # H - Hits allowed
    Column("earned_runs", Integer),                    # ER - Earned runs
    Column("walks", Integer),                          # BB - Walks (bases on balls)
    Column("strikeouts", Integer),                     # K - Strikeouts
    Column("home_runs_allowed", Integer, nullable=True),  # HR - Home runs allowed

    # Saves (for relief pitchers)
    Column("saves", Integer, nullable=True),

    # Quality Starts — a start where the pitcher goes 6+ IP with 3 or fewer ER.
    # Important fantasy baseball stat measuring pitcher reliability.
    Column("quality_starts", Integer, nullable=True),  # QS - Quality Starts

    # MLB Stats API player ID — used to link this pitcher to their game logs.
    Column("mlb_id", Integer, nullable=True),
)


# =============================================================================
# BATTER GAME LOGS TABLE
# =============================================================================
# Stores per-game batting stats fetched from the MLB Stats API.
# Each row = one player's stats for one game.
#
# This data powers the "rolling stats" feature: to compute a player's
# last-15-days batting average, we filter game logs by date range and
# aggregate: sum(hits) / sum(at_bats).
#
# Why a separate table instead of computing from season totals?
# Season totals are cumulative — you can't "subtract" the first 4 months
# to get the last 15 days. Per-game data lets us slice any time window.
#
# player_id stores the MLB API player ID (e.g., 592450 for Aaron Judge),
# NOT the auto-increment id from the players table. This decouples game
# logs from the players table so they can be fetched independently.
batter_game_logs = Table(
    "batter_game_logs",
    metadata,
    Column("id", Integer, primary_key=True),

    # MLB Stats API player ID — links to players.mlb_id
    Column("player_id", Integer, nullable=False),
    Column("player_name", String(100), nullable=False),
    Column("team", String(50)),

    # Game date in ISO format "YYYY-MM-DD" (e.g., "2024-07-15").
    # Stored as a string because ISO dates sort lexicographically,
    # so string comparison works correctly for date range filtering.
    Column("game_date", String(10), nullable=False),
    Column("opponent", String(50)),            # Opponent team name

    # Per-game batting stats — these get summed during rolling aggregation
    Column("at_bats", Integer, default=0),     # AB
    Column("hits", Integer, default=0),        # H
    Column("doubles", Integer, default=0),     # 2B
    Column("triples", Integer, default=0),     # 3B
    Column("home_runs", Integer, default=0),   # HR
    Column("rbi", Integer, default=0),         # RBI
    Column("runs", Integer, default=0),        # R - Runs scored
    Column("stolen_bases", Integer, default=0),# SB
    Column("walks", Integer, default=0),       # BB - Base on balls
    Column("strikeouts", Integer, default=0),  # K
    Column("hit_by_pitch", Integer, default=0),# HBP - needed for OBP calculation
    Column("sacrifice_flies", Integer, default=0),  # SF - needed for OBP calculation
)


# =============================================================================
# PITCHER GAME LOGS TABLE
# =============================================================================
# Stores per-game pitching stats for computing rolling pitcher averages.
# Each row = one pitcher's stats for one game appearance.
#
# Wins, losses, and saves are stored as 0 or 1 (per game) so they can
# be summed during aggregation to get totals over a time window.
#
# quality_start is computed when the data is fetched:
# QS = 1 if innings_pitched >= 6.0 AND earned_runs <= 3, else 0.
# This lets us sum QS over a rolling window without re-computing.
pitcher_game_logs = Table(
    "pitcher_game_logs",
    metadata,
    Column("id", Integer, primary_key=True),

    # MLB Stats API player ID — links to pitchers.mlb_id
    Column("player_id", Integer, nullable=False),
    Column("player_name", String(100), nullable=False),
    Column("team", String(50)),

    Column("game_date", String(10), nullable=False),  # "YYYY-MM-DD"
    Column("opponent", String(50)),

    # Per-game pitching stats
    Column("innings_pitched", Float, default=0.0),     # IP (e.g., 6.2 = 6 2/3 innings)
    Column("hits_allowed", Integer, default=0),        # H
    Column("earned_runs", Integer, default=0),         # ER
    Column("walks", Integer, default=0),               # BB
    Column("strikeouts", Integer, default=0),          # K
    Column("home_runs_allowed", Integer, default=0),   # HR allowed
    Column("wins", Integer, default=0),                # 0 or 1 for this game
    Column("losses", Integer, default=0),              # 0 or 1
    Column("saves", Integer, default=0),               # 0 or 1
    Column("quality_start", Integer, default=0),       # 0 or 1 (6+ IP, 3 or fewer ER)
    Column("pitches", Integer, default=0),             # Pitch count
)


# =============================================================================
# FANTASY LEAGUES TABLE (ESPN + Yahoo)
# =============================================================================
# Stores fantasy league configurations for both ESPN and Yahoo providers.
# Each row represents one league that the user has connected. The
# scoring_settings column stores the full scoring rules as a JSON string
# (serialized dict) fetched from the provider's API.
#
# Why store scoring_settings as a JSON string instead of a separate table?
# Scoring rules are a flat list of {statKey: point_value} pairs. Storing
# them as JSON keeps the schema simple and avoids a many-to-many join table
# for what is essentially configuration data that rarely changes.
#
# Authentication:
# - ESPN public leagues: only league_id is needed
# - ESPN private leagues: also need espn_s2 and swid cookies from the browser
#   (found in DevTools → Application → Cookies → espn.com)
# - Yahoo leagues: require OAuth 1.0a — consumer key + secret from a Yahoo
#   Developer app, plus access/refresh tokens obtained via the OAuth flow
fantasy_leagues = Table(
    "fantasy_leagues",
    metadata,
    Column("id", Integer, primary_key=True),

    # Provider identifier — "espn" or "yahoo".
    # Tells the app which API/logic to use for this league.
    # Nullable with default "espn" for backward compatibility with existing rows.
    Column("provider", String(20), nullable=True),

    # ESPN league ID — the numeric identifier from the ESPN fantasy league URL.
    # Example: https://fantasy.espn.com/baseball/league?leagueId=12345
    # Multiple rows can share the same league_id (e.g., different seasons).
    # Nullable because Yahoo leagues use yahoo_league_key instead.
    Column("league_id", Integer, nullable=True),

    # Human-readable league name fetched from ESPN/Yahoo (e.g., "Brian's Dynasty League").
    # This is displayed in the league selector dropdown in the frontend.
    Column("league_name", String(200), nullable=False),

    # The season year this scoring configuration applies to (e.g., 2025).
    Column("season_year", Integer, nullable=False),

    # JSON-serialized dict of scoring rules: {"statKey": pointValue, ...}
    # ESPN example: {"5": 5.0, "6": 2.0, "8": 1.0, "9": -1.0, ...}
    #   Each key is an ESPN stat ID (as a string), value is point value.
    # Yahoo example: {"HR": 5.0, "RBI": 1.0, "K": -1.0, ...}
    #   Each key is a Yahoo stat display_name, value is point value.
    # Stored as a string because SQLAlchemy Core + asyncpg handles Text
    # more reliably than PostgreSQL JSON columns with the databases library.
    Column("scoring_settings", String(5000), nullable=False),

    # --- ESPN-specific authentication ---
    # Optional ESPN authentication cookies for private leagues.
    # espn_s2: A long session cookie (~300+ chars) from espn.com
    # swid: A shorter GUID-format cookie like {XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}
    # For public leagues or Yahoo leagues, these are left null.
    Column("espn_s2", String(500), nullable=True),
    Column("swid", String(100), nullable=True),

    # --- Yahoo-specific fields ---
    # Yahoo league key — string format "431.l.123456" (game_id.l.league_id).
    # The game_id changes each season (e.g., 431 = 2025 MLB).
    # This is Yahoo's equivalent of ESPN's numeric league_id.
    Column("yahoo_league_key", String(50), nullable=True),

    # Yahoo OAuth 1.0a tokens — obtained through the OAuth authorization flow.
    # access_token: Used to authenticate API requests (~60 min lifespan)
    # refresh_token: Used to get a new access_token when it expires (long-lived)
    # token_expires_at: ISO timestamp of when the access_token expires,
    #   so we know when to refresh before making API calls.
    Column("yahoo_access_token", String(2000), nullable=True),
    Column("yahoo_refresh_token", String(2000), nullable=True),
    Column("yahoo_token_expires_at", String(30), nullable=True),

    # Timestamp of when this league was added (ISO format string "YYYY-MM-DD HH:MM:SS").
    Column("created_at", String(30), nullable=True),
)
