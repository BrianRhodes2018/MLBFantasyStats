"""
yahoo_fantasy.py - Yahoo Fantasy League Integration
=====================================================

This module handles communication with the Yahoo Fantasy Sports API and computes
fantasy points for each player based on Yahoo league-specific scoring settings.

Key concepts:
- Yahoo Fantasy uses OAuth 2.0 for authentication (unlike ESPN which uses cookies).
  Users need a Yahoo Developer app with Consumer Key + Secret.
- Yahoo stat categories use human-readable "display_name" values (e.g., "HR", "K")
  rather than numeric IDs like ESPN. This makes the mapping more intuitive.
- Each Yahoo fantasy league has custom scoring rules (stat_modifiers) that assign
  point values to each stat. We fetch these from the Yahoo API.
- Fantasy points are computed using Polars expressions, matching the exact same
  pattern as espn_fantasy.py for consistency.

Yahoo Fantasy API:
- Base URL: https://fantasysports.yahooapis.com/fantasy/v2/
- Requires OAuth 2.0 Bearer token for all requests
- Returns XML by default, but supports JSON via ?format=json parameter
- League keys have the format: {game_key}.l.{league_id} (e.g., "458.l.12345")
  where 458 is the 2025 MLB game key

OAuth 2.0 Flow (Authorization Code with out-of-band redirect):
1. User creates a Yahoo Developer app at developer.yahoo.com → gets Consumer Key + Secret
2. App generates an authorization URL → user opens it in browser → clicks "Agree"
3. Yahoo shows a verification code → user pastes it back into the app
4. App exchanges the code for access + refresh tokens
5. Access tokens expire after ~1 hour; refresh tokens are long-lived
"""

import httpx
import json
import base64
from typing import Optional
from datetime import datetime, timedelta
import polars as pl


# =============================================================================
# YAHOO OAUTH 2.0 CONFIGURATION
# =============================================================================
# These are Yahoo's standard OAuth 2.0 endpoints.
# All Yahoo apps (fantasy, mail, etc.) use the same auth endpoints.

YAHOO_AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
YAHOO_TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"

# The Yahoo Fantasy Sports API base URL.
# "v2" is the current API version. All fantasy endpoints branch off this.
YAHOO_API_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"

# The 2025 MLB game key. Yahoo assigns a unique game_key per sport per season.
# This changes each year — 2025 MLB = 458.
# League keys combine this with the league ID: "458.l.12345"
YAHOO_MLB_GAME_KEY_2025 = "458"


# =============================================================================
# YAHOO STAT DISPLAY_NAME → OUR DATABASE COLUMN MAPPING
# =============================================================================
# Yahoo Fantasy uses human-readable "display_name" strings for stats (e.g., "HR",
# "K", "W") rather than numeric IDs like ESPN. This makes the mapping more
# intuitive and less error-prone.
#
# How to read this:
#   Yahoo display_name "HR" → "home_runs" column in our players table
#   Yahoo display_name "K" (pitching) → "strikeouts" column in our pitchers table
#
# The stat_categories response from Yahoo includes position_type ("B" for batting,
# "P" for pitching) so we know which map to use.
#
# Source: Yahoo Fantasy API stat_categories endpoint and yahoo_fantasy_api package.

# Batting stat display_names → players table column names
YAHOO_BATTING_STAT_MAP = {
    "AB": "at_bats",            # At Bats
    "H": "hits",                # Hits
    "AVG": "batting_average",   # Batting Average (rate stat)
    "1B": "singles",            # Singles (COMPUTED: H - 2B - 3B - HR, not stored)
    "2B": "doubles",            # Doubles
    "3B": "triples",            # Triples
    "HR": "home_runs",          # Home Runs
    "R": "runs",                # Runs Scored
    "RBI": "rbi",               # Runs Batted In
    "SB": "stolen_bases",       # Stolen Bases
    "CS": "caught_stealing",    # Caught Stealing (usually negative points)
    "BB": "walks",              # Walks (Bases on Balls)
    "HBP": "hit_by_pitch",     # Hit By Pitch
    "SF": "sacrifice_flies",   # Sacrifice Flies
    "K": "strikeouts",          # Strikeouts (batting, usually negative)
    "TB": "total_bases",        # Total Bases
    "OBP": "obp",               # On-Base Percentage (rate stat, computed)
    "OPS": "ops",                # On-base + Slugging (rate stat)
    "XBH": "xbh",               # Extra Base Hits (COMPUTED: 2B + 3B + HR)
    # "NSB": Net Stolen Bases (SB - CS, not stored)
    # "SLG": Slugging Percentage (not stored)
    # "GP": Games Played (not stored)
    # "PA": Plate Appearances (not stored)
    # "GIDP": Grounded Into Double Play (not stored)
    # "SLAM": Grand Slam Home Runs (not stored)
}

# Pitching stat display_names → pitchers table column names
YAHOO_PITCHING_STAT_MAP = {
    "W": "wins",                # Wins
    "L": "losses",              # Losses (usually negative points)
    "SV": "saves",              # Saves
    "HLD": "holds",             # Holds (not stored in our DB currently)
    "K": "strikeouts",          # Strikeouts (pitching, usually positive)
    "ERA": "era",               # Earned Run Average (rate stat)
    "WHIP": "whip",             # Walks + Hits per IP (rate stat)
    "IP": "innings_pitched",    # Innings Pitched (special: baseball notation)
    "OUT": "outs",              # Outs (IP × 3, SPECIAL HANDLING like ESPN)
    "H": "hits_allowed",        # Hits Allowed
    "ER": "earned_runs",        # Earned Runs
    "HR": "home_runs_allowed",  # Home Runs Allowed (usually negative)
    "BB": "walks",              # Walks (pitching)
    "QS": "quality_starts",     # Quality Starts (6+ IP, 3 or fewer ER)
    "GS": "games_started",      # Games Started
    # "CG": Complete Games (not stored)
    # "SHO": Shutouts (not stored)
    # "APP": Pitching Appearances (not stored)
    # "BSV": Blown Saves (not stored)
    # "WP": Wild Pitches (not stored)
    # "BLK": Balks (not stored)
}


# =============================================================================
# YAHOO OAUTH 2.0 HELPERS
# =============================================================================
# These functions implement the OAuth 2.0 Authorization Code flow for Yahoo.
# The flow works in two stages:
# 1. get_yahoo_auth_url() → generates a URL the user opens in their browser
# 2. exchange_yahoo_code() → trades the verification code for API tokens


def get_yahoo_auth_url(consumer_key: str) -> str:
    """
    Generate the Yahoo OAuth 2.0 authorization URL.

    The user opens this URL in their browser, logs into Yahoo, and
    clicks "Agree" to authorize the app. Yahoo then shows them a
    verification code to paste back into our app.

    We use "oob" (out-of-band) as the redirect_uri because this is a
    desktop/local app — there's no public callback URL for Yahoo to
    redirect to. Instead, Yahoo displays the code directly to the user.

    Args:
        consumer_key: The Consumer Key from the Yahoo Developer app

    Returns:
        The full authorization URL for the user to open in their browser
    """
    # Build the authorization URL with required OAuth 2.0 parameters.
    # response_type=code tells Yahoo we want an authorization code
    # (not an implicit token grant, which is less secure).
    params = {
        "client_id": consumer_key,
        "redirect_uri": "oob",
        "response_type": "code",
        "language": "en-us",
    }
    # Build the query string manually to avoid URL encoding issues
    query_string = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{YAHOO_AUTH_URL}?{query_string}"


async def exchange_yahoo_code(
    consumer_key: str,
    consumer_secret: str,
    authorization_code: str,
) -> dict:
    """
    Exchange a Yahoo authorization code for access + refresh tokens.

    After the user authorizes in the browser, Yahoo gives them a verification
    code. This function sends that code to Yahoo's token endpoint along with
    the app credentials to get API tokens.

    Yahoo's token endpoint uses HTTP Basic Authentication:
    - Username = Consumer Key
    - Password = Consumer Secret
    - Encoded as Base64 in the Authorization header

    Args:
        consumer_key: The Consumer Key from the Yahoo Developer app
        consumer_secret: The Consumer Secret from the Yahoo Developer app
        authorization_code: The verification code the user received from Yahoo

    Returns:
        dict with keys:
        - "access_token": str — Bearer token for API requests (~1 hour lifespan)
        - "refresh_token": str — Long-lived token for getting new access tokens
        - "expires_in": int — Seconds until the access token expires (typically 3600)
        - "token_type": str — Always "bearer"

    Raises:
        httpx.HTTPStatusError: If Yahoo rejects the code (expired, invalid, etc.)
    """
    # Yahoo requires HTTP Basic Authentication for the token endpoint.
    # Base64 encode "consumer_key:consumer_secret" as the auth header.
    credentials = f"{consumer_key}:{consumer_secret}"
    basic_auth = base64.b64encode(credentials.encode()).decode()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            YAHOO_TOKEN_URL,
            headers={
                "Authorization": f"Basic {basic_auth}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "authorization_code",
                "redirect_uri": "oob",
                "code": authorization_code,
            },
            timeout=15.0,
        )
        response.raise_for_status()
        return response.json()


async def refresh_yahoo_token(
    consumer_key: str,
    consumer_secret: str,
    refresh_token: str,
) -> dict:
    """
    Refresh an expired Yahoo access token using the refresh token.

    Yahoo access tokens expire after ~1 hour. When they expire, we use the
    refresh token (which is long-lived) to get a new access token without
    requiring the user to re-authorize.

    This is called automatically before making API requests if the stored
    token has expired.

    Args:
        consumer_key: The Consumer Key from the Yahoo Developer app
        consumer_secret: The Consumer Secret from the Yahoo Developer app
        refresh_token: The refresh token from the original token exchange

    Returns:
        dict with the same structure as exchange_yahoo_code() — a new
        access_token and possibly a new refresh_token.

    Raises:
        httpx.HTTPStatusError: If the refresh token is invalid or revoked
    """
    credentials = f"{consumer_key}:{consumer_secret}"
    basic_auth = base64.b64encode(credentials.encode()).decode()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            YAHOO_TOKEN_URL,
            headers={
                "Authorization": f"Basic {basic_auth}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "refresh_token",
                "redirect_uri": "oob",
                "refresh_token": refresh_token,
            },
            timeout=15.0,
        )
        response.raise_for_status()
        return response.json()


# =============================================================================
# YAHOO API COMMUNICATION
# =============================================================================

async def fetch_yahoo_league_settings(
    access_token: str,
    league_key: str,
) -> dict:
    """
    Fetch a Yahoo fantasy league's scoring settings from the Yahoo API.

    This calls the Yahoo Fantasy API's league settings endpoint, which returns
    the league name, scoring categories, and point values (stat_modifiers).

    Yahoo's response structure:
    - stat_categories: List of stats with display_name, position_type, and whether
      they're scoring or display-only
    - stat_modifiers: List of stat_id → point_value pairs for scoring stats

    We combine these to build a clean mapping of display_name → point_value,
    which is what we store in the database as scoring_settings.

    Args:
        access_token: A valid Yahoo OAuth 2.0 Bearer token
        league_key: The Yahoo league key (e.g., "458.l.12345")

    Returns:
        dict with keys:
        - "league_name": str — the league's display name
        - "scoring_items": dict — {"display_name": point_value, ...}
          Keyed by display_name (e.g., "HR", "K") for batting and pitching
          The position_type is included as a prefix for stats that have the
          same display_name in both batting and pitching (e.g., "B_K" vs "P_K")
        - "season_year": int

    Raises:
        httpx.HTTPStatusError: If the Yahoo API returns an error
        Exception: For network errors or unexpected response format
    """
    url = f"{YAHOO_API_BASE}/league/{league_key}/settings"

    async with httpx.AsyncClient() as client:
        response = await client.get(
            url,
            params={"format": "json"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15.0,
        )
        response.raise_for_status()
        data = response.json()

    # Navigate the Yahoo Fantasy API response structure.
    # The response is deeply nested — this is just how Yahoo's API works.
    # Structure: fantasy_content → league → [league_info, settings_wrapper]
    fantasy_content = data.get("fantasy_content", {})
    league_data = fantasy_content.get("league", [])

    # league_data is a list: [league_info_dict, settings_wrapper_dict]
    # First element has league metadata (name, season, etc.)
    league_info = league_data[0] if len(league_data) > 0 else {}
    league_name = league_info.get("name", f"Yahoo League")
    season_year = int(league_info.get("season", 2025))

    # Second element has the settings (stat_categories + stat_modifiers)
    settings_wrapper = league_data[1] if len(league_data) > 1 else {}
    settings_list = settings_wrapper.get("settings", [{}])
    settings = settings_list[0] if settings_list else {}

    # Extract stat categories — these tell us which stats are enabled
    # and their display_name, position_type, and stat_id
    stat_categories_data = settings.get("stat_categories", {})
    stat_categories = stat_categories_data.get("stats", [])

    # Build a lookup: stat_id → {display_name, position_type}
    stat_info = {}
    for item in stat_categories:
        stat = item.get("stat", {})
        stat_id = stat.get("stat_id")
        display_name = stat.get("display_name", "")
        position_type = stat.get("position_type", "")  # "B" or "P"
        is_display_only = stat.get("is_only_display_stat", "0") == "1"

        if stat_id is not None and not is_display_only:
            stat_info[stat_id] = {
                "display_name": display_name,
                "position_type": position_type,
            }

    # Extract stat modifiers — these are the point values for each stat
    stat_modifiers_data = settings.get("stat_modifiers", {})
    stat_modifiers = stat_modifiers_data.get("stats", [])

    # Build the final scoring_items mapping.
    # We prefix the display_name with "B_" or "P_" to disambiguate stats
    # that share the same abbreviation between batting and pitching
    # (e.g., "K" = Strikeouts for both, "H" = Hits vs Hits Allowed,
    #  "BB" = Walks for both, "HR" = Home Runs vs HR Allowed).
    scoring_items = {}
    for item in stat_modifiers:
        stat = item.get("stat", {})
        stat_id = stat.get("stat_id")
        point_value = float(stat.get("value", 0))

        # Look up the display_name and position_type from stat_categories
        info = stat_info.get(stat_id)
        if info:
            # Use "B_HR" / "P_HR" format to avoid ambiguity
            key = f"{info['position_type']}_{info['display_name']}"
            scoring_items[key] = point_value

    return {
        "league_name": league_name,
        "scoring_items": scoring_items,
        "season_year": season_year,
    }


# =============================================================================
# FANTASY POINTS COMPUTATION (POLARS)
# =============================================================================
# These functions compute fantasy points using Polars expressions, following
# the same pattern as espn_fantasy.py for consistency.
#
# The key difference from ESPN: Yahoo scoring_items keys are prefixed display
# names like "B_HR" (batting Home Runs) or "P_K" (pitching Strikeouts),
# so we strip the prefix and look up the column in the appropriate stat map.
#
#   fantasy_pts = SUM(player_stat_value * league_point_value) for each category


def compute_yahoo_fantasy_points_batters(
    df: pl.DataFrame,
    scoring_items: dict,
) -> pl.DataFrame:
    """
    Add a 'fantasy_pts' column to a batter DataFrame using Yahoo scoring rules.

    Same Polars pattern as espn_fantasy.py's compute_fantasy_points_batters().
    The only difference is how we look up stat columns — Yahoo uses display_name
    keys prefixed with "B_" (e.g., "B_HR" → "home_runs").

    Handles special cases:
    - "singles" (1B): Computed as hits - doubles - triples - home_runs
    - "xbh" (XBH): Computed as doubles + triples + home_runs
    - "obp" (OBP): Computed inline from H, BB, HBP, AB, SF
    - Missing columns: If a scored stat isn't in our data, it's skipped

    Args:
        df: Polars DataFrame of batter data
        scoring_items: dict of {"B_display_name": point_value, ...}

    Returns:
        The same DataFrame with an additional "fantasy_pts" column
    """
    if df.is_empty():
        return df.with_columns(pl.lit(None).cast(pl.Float64).alias("fantasy_pts"))

    points_expr = pl.lit(0.0)

    for stat_key, point_value in scoring_items.items():
        # Only process batting stats (prefixed with "B_")
        if not stat_key.startswith("B_"):
            continue

        # Strip the "B_" prefix to get the Yahoo display_name
        display_name = stat_key[2:]

        # Skip stats that aren't in our batting stat map
        if display_name not in YAHOO_BATTING_STAT_MAP:
            continue

        col_name = YAHOO_BATTING_STAT_MAP[display_name]

        # --- Special case: Singles (1B, computed, not stored) ---
        if col_name == "singles":
            if all(c in df.columns for c in ["hits", "doubles", "triples", "home_runs"]):
                singles_expr = (
                    pl.col("hits").fill_null(0)
                    - pl.col("doubles").fill_null(0)
                    - pl.col("triples").fill_null(0)
                    - pl.col("home_runs").fill_null(0)
                )
                points_expr = points_expr + (singles_expr * point_value)
            continue

        # --- Special case: XBH (Extra Base Hits, computed, not stored) ---
        if col_name == "xbh":
            if all(c in df.columns for c in ["doubles", "triples", "home_runs"]):
                xbh_expr = (
                    pl.col("doubles").fill_null(0)
                    + pl.col("triples").fill_null(0)
                    + pl.col("home_runs").fill_null(0)
                )
                points_expr = points_expr + (xbh_expr * point_value)
            continue

        # --- Special case: OBP (On-Base Percentage, computed) ---
        if col_name == "obp":
            if all(c in df.columns for c in ["batting_average", "at_bats", "walks", "hit_by_pitch", "sacrifice_flies"]):
                hits_expr = (pl.col("batting_average").fill_null(0.0) * pl.col("at_bats").fill_null(0)).round(0)
                numerator = hits_expr + pl.col("walks").fill_null(0) + pl.col("hit_by_pitch").fill_null(0)
                denominator = (
                    pl.col("at_bats").fill_null(0)
                    + pl.col("walks").fill_null(0)
                    + pl.col("hit_by_pitch").fill_null(0)
                    + pl.col("sacrifice_flies").fill_null(0)
                )
                obp_expr = pl.when(denominator > 0).then(numerator / denominator).otherwise(0.0)
                points_expr = points_expr + (obp_expr * point_value)
            continue

        # --- Standard counting stats ---
        if col_name in df.columns:
            points_expr = points_expr + (
                pl.col(col_name).cast(pl.Float64).fill_null(0.0) * point_value
            )

    return df.with_columns(
        points_expr.round(1).alias("fantasy_pts")
    )


def compute_yahoo_fantasy_points_pitchers(
    df: pl.DataFrame,
    scoring_items: dict,
) -> pl.DataFrame:
    """
    Add a 'fantasy_pts' column to a pitcher DataFrame using Yahoo scoring rules.

    Same Polars pattern as espn_fantasy.py's compute_fantasy_points_pitchers().
    Yahoo pitching stats are prefixed with "P_" (e.g., "P_K" → "strikeouts").

    Handles special cases:
    - "outs" (OUT): Yahoo stores outs like ESPN — need to convert from our
      baseball notation innings_pitched column (e.g., 207.0 → 621 outs).
    - "innings_pitched" (IP): Direct column match, but Yahoo scores it as
      actual innings (not outs), so no conversion needed.

    Args:
        df: Polars DataFrame of pitcher data
        scoring_items: dict of {"P_display_name": point_value, ...}

    Returns:
        The same DataFrame with an additional "fantasy_pts" column
    """
    if df.is_empty():
        return df.with_columns(pl.lit(None).cast(pl.Float64).alias("fantasy_pts"))

    points_expr = pl.lit(0.0)

    for stat_key, point_value in scoring_items.items():
        # Only process pitching stats (prefixed with "P_")
        if not stat_key.startswith("P_"):
            continue

        # Strip the "P_" prefix to get the Yahoo display_name
        display_name = stat_key[2:]

        # Skip stats that aren't in our pitching stat map
        if display_name not in YAHOO_PITCHING_STAT_MAP:
            continue

        col_name = YAHOO_PITCHING_STAT_MAP[display_name]

        # --- Special case: OUTS (OUT) ---
        # Same handling as ESPN statId 34. Yahoo's "OUT" stat counts total outs
        # (IP × 3). Our DB stores innings_pitched in baseball notation.
        # Conversion: outs = floor(IP) * 3 + round((IP - floor(IP)) * 10)
        if col_name == "outs":
            if "innings_pitched" in df.columns:
                ip_col = pl.col("innings_pitched").cast(pl.Float64).fill_null(0.0)
                outs_expr = (
                    ip_col.floor() * 3
                    + ((ip_col - ip_col.floor()) * 10).round(0)
                )
                points_expr = points_expr + (outs_expr * point_value)
            continue

        # --- Standard pitching stats ---
        if col_name in df.columns:
            points_expr = points_expr + (
                pl.col(col_name).cast(pl.Float64).fill_null(0.0) * point_value
            )

    return df.with_columns(
        points_expr.round(1).alias("fantasy_pts")
    )
