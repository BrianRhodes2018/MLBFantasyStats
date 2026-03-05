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
