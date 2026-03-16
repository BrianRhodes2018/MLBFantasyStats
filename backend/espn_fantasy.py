"""
espn_fantasy.py - ESPN Fantasy League Integration
===================================================

This module handles communication with the ESPN Fantasy API and computes
fantasy points for each player based on league-specific scoring settings.

Key concepts:
- ESPN Fantasy Baseball uses numeric "statId" values to identify statistics.
  For example, statId 5 = Home Runs, statId 48 = Strikeouts (pitching).
- Each fantasy league has custom scoring rules that assign point values
  to each statId. We fetch these rules from the ESPN Fantasy API.
- Fantasy points are computed using Polars expressions, matching the existing
  pattern for computed stats like OBP, Power Index, and K/9.
- Points leagues: Each stat has a point value (e.g., HR = 5 pts, K = -1 pt).
  A player's total fantasy points = SUM(stat_value * point_value) across all
  scored categories.

ESPN Fantasy API:
- Base URL: https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/...
- No API key needed for public leagues
- Private leagues require espn_s2 + SWID cookies from a logged-in browser
- The API is undocumented (community reverse-engineered) but stable

Authentication for private leagues:
1. Log into ESPN Fantasy in your browser
2. Open DevTools → Application → Cookies → espn.com
3. Copy the values for "espn_s2" and "SWID" cookies
4. Provide these when connecting the league in the app
"""

import httpx
import json
from typing import Optional
import polars as pl


# =============================================================================
# ESPN STAT ID → OUR DATABASE COLUMN MAPPING
# =============================================================================
# ESPN Fantasy Baseball uses numeric stat IDs internally. This dict maps each
# ESPN statId to the corresponding column name in our database tables.
#
# How to read this:
#   ESPN statId 5 → "home_runs" column in our players table
#   ESPN statId 48 → "strikeouts" column in our pitchers table
#
# If a league scores a stat we don't track, that stat is simply skipped
# (the player won't earn/lose points for that category).
#
# Source: These IDs come from the espn-api Python package's constant.py
# (https://github.com/cwendt94/espn-api) and were verified against actual
# ESPN API player stat responses for known players (Ohtani, Judge, Webb).
#
# IMPORTANT: The stat IDs are GLOBALLY UNIQUE across batting and pitching —
# batting stats use IDs 0–31 and pitching stats use IDs 32–66. They do NOT
# overlap, so we can safely check both maps without conflict.

# Batting stat IDs → players table column names
ESPN_BATTING_STAT_MAP = {
    0: "at_bats",           # AB - At Bats
    1: "hits",              # H - Hits
    2: "batting_average",   # AVG - Batting Average (rate stat, unusual in points leagues)
    3: "doubles",           # 2B - Doubles
    4: "triples",           # 3B - Triples
    5: "home_runs",         # HR - Home Runs
    6: "xbh",               # XBH - Extra Base Hits (COMPUTED: 2B + 3B + HR, not stored)
    7: "singles",           # 1B - Singles (COMPUTED: H - 2B - 3B - HR, not stored)
    8: "total_bases",       # TB - Total Bases
    # 9: SLG - Slugging (not stored in our DB)
    10: "walks",            # BB - Walks (Bases on Balls)
    # 11: IBB - Intentional Walks (not stored)
    12: "hit_by_pitch",     # HBP - Hit By Pitch
    13: "sacrifice_flies",  # SF - Sacrifice Flies
    # 14: SH - Sacrifice Hits (not stored)
    # 15: SAC - Sacrifices (not stored)
    # 16: PA - Plate Appearances (not stored as column)
    17: "obp",              # OBP - On-Base Percentage (rate stat, computed in Polars)
    # 18: OPS - On-Base Plus Slugging (not stored)
    # 19: RC - Runs Created (not stored)
    20: "runs",             # R - Runs Scored
    21: "rbi",              # RBI - Runs Batted In
    23: "stolen_bases",     # SB - Stolen Bases
    24: "caught_stealing",  # CS - Caught Stealing (usually negative points)
    # 25: SB-CS - Net Stolen Bases (not stored, could compute SB - CS)
    # 26: GDP - Grounded Into Double Play (not stored)
    27: "strikeouts",       # SO/K - Strikeouts (usually negative points)
    # 28: PS - Pitches Seen (not stored)
    # 29: PPA - Pitches Per Plate Appearance (not stored)
    # 31: CYC - Cycles (not stored)
}

# Pitching stat IDs → pitchers table column names
#
# IMPORTANT NOTE ON STAT ID 34 (OUTS):
# ESPN stores Innings Pitched as total OUTS (IP × 3), not actual innings.
# Example: 207 IP = 621 outs in ESPN's system.
# When the league scores "1 point per out" (statId 34), it means 3 points
# per full inning. Our DB stores IP in baseball notation (e.g., 207.0),
# so we need a special conversion in the compute function.
ESPN_PITCHING_STAT_MAP = {
    32: "games_pitched",        # GP - Games Pitched / Appearances (not stored)
    33: "games_started",        # GS - Games Started (not stored)
    34: "outs",                 # OUTS - Total Outs (IP × 3, SPECIAL HANDLING NEEDED)
    # 35: TBF - Total Batters Faced (not stored)
    # 36: P - Total Pitches (not stored)
    37: "hits_allowed",         # H - Hits Allowed
    # 38: OBA - Opponent Batting Average (not stored)
    39: "walks",                # BB - Walks (pitching)
    # 40: P_IBB - Intentional Walks pitched (not stored)
    41: "whip",                 # WHIP - Walks + Hits per IP (rate stat)
    # 42: HBP - Hit By Pitch pitching (not stored)
    # 43: OOBP - Opponent OBP (not stored)
    # 44: P_R - Runs Allowed total (not stored)
    45: "earned_runs",          # ER - Earned Runs
    46: "home_runs_allowed",    # HRA - Home Runs Allowed (usually negative points)
    47: "era",                  # ERA - Earned Run Average (rate stat)
    48: "strikeouts",           # K - Strikeouts (pitching, usually positive points)
    # 49: K/9 - Strikeouts per 9 innings (not stored as column)
    # 50: WP - Wild Pitches (not stored)
    # 51: BLK - Balks (not stored)
    # 52: PK - Pickoffs (not stored)
    53: "wins",                 # W - Wins
    54: "losses",               # L - Losses (usually negative points)
    # 55: WPCT - Win Percentage (not stored)
    # 56: SVO - Save Opportunities (not stored)
    57: "saves",                # SV - Saves
    # 58: BLSV - Blown Saves (not stored)
    # 59: SV% - Save Percentage (not stored)
    # 60: HLD - Holds (not stored in our DB)
    # 62: CG - Complete Games (not stored)
    63: "quality_starts",       # QS - Quality Starts (6+ IP, 3 or fewer ER)
    # 65: NH - No-Hitters (not stored)
    # 66: PG - Perfect Games (not stored)
}


# =============================================================================
# ESPN API COMMUNICATION
# =============================================================================

# The base URL for the ESPN Fantasy API.
# "flb" stands for "Fantasy League Baseball" (as opposed to "ffl" for football).
# The URL structure is: .../games/flb/seasons/{year}/segments/0/leagues/{leagueId}
ESPN_BASE_URL = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb"
    "/seasons/{year}/segments/0/leagues/{league_id}"
)


async def fetch_league_settings(
    league_id: int,
    season_year: int = 2025,
    espn_s2: Optional[str] = None,
    swid: Optional[str] = None,
) -> dict:
    """
    Fetch a fantasy league's scoring settings from the ESPN API.

    This calls the ESPN Fantasy API with the mSettings "view", which returns
    the league configuration including scoring rules, team count, and league name.

    How it works:
    1. Build the ESPN API URL with the league ID and season year
    2. Add authentication cookies if provided (needed for private leagues)
    3. Send a GET request with ?view=mSettings query parameter
    4. Parse the response to extract scoring items and league name

    The response includes a "scoringItems" array where each item has:
    - statId: ESPN's numeric identifier for the stat
    - pointsOverride: Custom point value (if the league overrode the default)
    - points: Default point value for this stat

    Args:
        league_id: The ESPN fantasy league ID (from the league URL)
        season_year: The season year (default 2025)
        espn_s2: The espn_s2 cookie value for private leagues (optional)
        swid: The SWID cookie value for private leagues (optional)

    Returns:
        dict with keys:
        - "league_name": str — the league's display name
        - "scoring_items": dict — {statId_as_string: point_value, ...}
        - "season_year": int

    Raises:
        httpx.HTTPStatusError: If the ESPN API returns an error (e.g., 404 for invalid league)
        Exception: For network errors or unexpected response format
    """
    url = ESPN_BASE_URL.format(year=season_year, league_id=league_id)

    # Build cookies dict for private leagues.
    # Public leagues respond without any authentication — the API is open.
    # Private leagues return a 401/404 without the correct cookies.
    cookies = {}
    if espn_s2:
        cookies["espn_s2"] = espn_s2
    if swid:
        # ESPN expects the SWID cookie with curly braces: {XXXXXXXX-...}
        # Make sure we include them if the user forgot
        swid_value = swid if swid.startswith("{") else f"{{{swid}}}"
        cookies["SWID"] = swid_value

    # Use httpx.AsyncClient for async HTTP requests (compatible with FastAPI's
    # async endpoints). The timeout prevents hanging if ESPN is slow.
    async with httpx.AsyncClient() as client:
        response = await client.get(
            url,
            params={"view": "mSettings"},
            cookies=cookies if cookies else None,
            timeout=15.0,
        )
        # raise_for_status() throws an exception for 4xx/5xx responses
        # (e.g., 401 Unauthorized for private leagues without valid cookies)
        response.raise_for_status()
        data = response.json()

    # Extract the league name from the settings object.
    # The ESPN API response structure: { "settings": { "name": "...", "scoringSettings": {...} } }
    settings = data.get("settings", {})
    league_name = settings.get("name", f"League {league_id}")

    # Extract scoring items — these define how fantasy points are calculated.
    # Each item in the array represents one scored stat category with its point value.
    scoring_settings = settings.get("scoringSettings", {})
    scoring_items_raw = scoring_settings.get("scoringItems", [])

    # Build a clean mapping: {"statId": point_value}
    # We use string keys because JSON serialization (for database storage) handles
    # strings cleanly, and we convert back to int when computing points.
    scoring_items = {}
    for item in scoring_items_raw:
        stat_id = str(item.get("statId", ""))
        # "pointsOverride" is used when the league has customized the default value.
        # If not customized, fall back to the default "points" value.
        # Some items may have a "points" field directly with the value.
        points = item.get("pointsOverride", {})
        if isinstance(points, dict):
            # pointsOverride can be a dict like {"16": 5.0} — take the first value
            # or fall back to the "points" field
            points = list(points.values())[0] if points else item.get("points", 0)
        if stat_id:
            scoring_items[stat_id] = float(points)

    return {
        "league_name": league_name,
        "scoring_items": scoring_items,
        "season_year": season_year,
    }


# =============================================================================
# FANTASY POINTS COMPUTATION (POLARS)
# =============================================================================
# These functions compute fantasy points using Polars expressions, following
# the same pattern as existing computed stats (OBP, Power Index, K/9, etc.)
# in main.py. The key idea:
#
#   fantasy_pts = SUM(player_stat_value * league_point_value) for each category
#
# For example, if the league scores:
#   HR = 5 pts, RBI = 1 pt, SB = 2 pts, K = -1 pt
# Then Aaron Judge (58 HR, 144 RBI, 10 SB, 142 K) would get:
#   58*5 + 144*1 + 10*2 + 142*(-1) = 290 + 144 + 20 - 142 = 312 pts


def compute_fantasy_points_batters(
    df: pl.DataFrame,
    scoring_items: dict,
) -> pl.DataFrame:
    """
    Add a 'fantasy_pts' column to a batter DataFrame using Polars expressions.

    This follows the same pattern as OBP and Power Index computations in main.py:
    we use .with_columns() to add a new derived column to the existing DataFrame.

    Handles special cases:
    - "singles" (statId 7): Computed as hits - doubles - triples - home_runs
      since we don't store singles directly in the database.
    - "xbh" (statId 6): Computed as doubles + triples + home_runs.
    - "obp" (statId 17): Computed inline from H, BB, HBP, AB, SF.
    - Rate stats (AVG, OBP): These are already decimal values (e.g., 0.322).
      Some leagues score them with large multipliers (e.g., OBP * 250).
    - Missing columns: If a scored stat isn't in our data, it's skipped.
    - Null values: Uses .fill_null(0) to treat missing data as zero.

    Args:
        df: Polars DataFrame of batter data (from database or search results)
        scoring_items: dict of {"statId": point_value} from the league settings

    Returns:
        The same DataFrame with an additional "fantasy_pts" column (Float64),
        rounded to 1 decimal place.
    """
    # Handle empty DataFrame — return with a null fantasy_pts column
    if df.is_empty():
        return df.with_columns(pl.lit(None).cast(pl.Float64).alias("fantasy_pts"))

    # Build a Polars expression that sums (stat_column * point_value) for each
    # scoring item that maps to a column we have.
    #
    # We start with 0.0 and add each scored stat's contribution:
    #   0.0 + (HR * 5) + (RBI * 1) + (SB * 2) + (K * -1) + ...
    #
    # This approach is equivalent to a dot product between the player's stat
    # vector and the league's point-value vector.
    points_expr = pl.lit(0.0)

    for stat_id_str, point_value in scoring_items.items():
        stat_id = int(stat_id_str)

        # Skip stats that aren't in our batting stat map (e.g., pitching stats)
        if stat_id not in ESPN_BATTING_STAT_MAP:
            continue

        col_name = ESPN_BATTING_STAT_MAP[stat_id]

        # --- Special case: Singles (statId 7, computed, not stored) ---
        # Singles = Hits - Doubles - Triples - Home Runs
        # We compute this on-the-fly using a Polars expression
        if col_name == "singles":
            # Only compute if we have the necessary columns
            if all(c in df.columns for c in ["hits", "doubles", "triples", "home_runs"]):
                singles_expr = (
                    pl.col("hits").fill_null(0)
                    - pl.col("doubles").fill_null(0)
                    - pl.col("triples").fill_null(0)
                    - pl.col("home_runs").fill_null(0)
                )
                points_expr = points_expr + (singles_expr * point_value)
            continue

        # --- Special case: XBH (statId 6, computed, not stored) ---
        # Extra Base Hits = Doubles + Triples + Home Runs
        if col_name == "xbh":
            if all(c in df.columns for c in ["doubles", "triples", "home_runs"]):
                xbh_expr = (
                    pl.col("doubles").fill_null(0)
                    + pl.col("triples").fill_null(0)
                    + pl.col("home_runs").fill_null(0)
                )
                points_expr = points_expr + (xbh_expr * point_value)
            continue

        # --- Special case: OBP (statId 17, computed stat, not stored in DB) ---
        # OBP is computed by Polars in main.py, so it won't be in the DataFrame
        # from the database. If a league scores OBP, we compute it here too.
        if col_name == "obp":
            # Compute OBP inline: (H + BB + HBP) / (AB + BB + HBP + SF)
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
        # Check if the column exists in our DataFrame (it might not if the data
        # is from a search result with limited columns)
        if col_name in df.columns:
            # .fill_null(0) ensures null values don't propagate through the sum.
            # For rate stats like batting_average, this treats unknown as 0.
            points_expr = points_expr + (
                pl.col(col_name).cast(pl.Float64).fill_null(0.0) * point_value
            )

    # Add the computed fantasy_pts column, rounded to 1 decimal place
    return df.with_columns(
        points_expr.round(1).alias("fantasy_pts")
    )


def compute_fantasy_points_pitchers(
    df: pl.DataFrame,
    scoring_items: dict,
) -> pl.DataFrame:
    """
    Add a 'fantasy_pts' column to a pitcher DataFrame using Polars expressions.

    Same pattern as compute_fantasy_points_batters but uses the pitching stat map.
    Pitching fantasy scoring typically includes:
    - Positive: W, SV, K, QS, IP
    - Negative: L, ER, H, BB, HRA

    Handles special cases:
    - "outs" (statId 34): ESPN stores IP as total outs (IP × 3). The point value
      from ESPN is "per out", so we convert our innings_pitched column to outs
      before multiplying. Baseball notation 205.1 = 205 full innings + 1 out = 616.
    - Missing columns: If a scored stat isn't in our data (e.g., holds), it's
      skipped. The player won't earn/lose points for that category.

    Example with this user's league scoring:
      W=3pts, K=1pt, SV=5pts, L=-3pts, ER=-2pts, H=-1pt, BB=-1pt,
      OUTS=1pt/out, QS=3pts
    For Logan Webb (15W, 224K, 0SV, 11L, 74ER, 210H, 46BB, 207IP=621 outs, 22QS):
      15*3 + 224*1 + 0*5 + 11*(-3) + 74*(-2) + 210*(-1) + 46*(-1) + 621*1 + 22*3
      = 45 + 224 + 0 - 33 - 148 - 210 - 46 + 621 + 66 = 519 pts

    Args:
        df: Polars DataFrame of pitcher data
        scoring_items: dict of {"statId": point_value} from the league settings

    Returns:
        The same DataFrame with an additional "fantasy_pts" column (Float64)
    """
    if df.is_empty():
        return df.with_columns(pl.lit(None).cast(pl.Float64).alias("fantasy_pts"))

    points_expr = pl.lit(0.0)

    for stat_id_str, point_value in scoring_items.items():
        stat_id = int(stat_id_str)

        # Skip stats that aren't in our pitching stat map (e.g., batting stats)
        if stat_id not in ESPN_PITCHING_STAT_MAP:
            continue

        col_name = ESPN_PITCHING_STAT_MAP[stat_id]

        # --- Special case: OUTS (statId 34) ---
        # ESPN stores Innings Pitched as total outs (IP × 3). Our DB stores
        # innings_pitched in baseball notation (e.g., 207.0, 205.1, 195.2).
        #
        # Baseball notation: the decimal part is thirds, NOT tenths:
        #   205.0 = 205 innings = 615 outs
        #   205.1 = 205 + 1/3 innings = 616 outs
        #   205.2 = 205 + 2/3 innings = 617 outs
        #
        # Conversion: outs = floor(IP) * 3 + round((IP - floor(IP)) * 10)
        # This correctly handles the baseball notation quirk.
        if col_name == "outs":
            if "innings_pitched" in df.columns:
                ip_col = pl.col("innings_pitched").cast(pl.Float64).fill_null(0.0)
                # Convert baseball IP notation to total outs:
                # floor(IP) gives full innings, multiply by 3 for outs
                # The fractional part (.1, .2) represents additional outs
                # so multiply by 10 to get the out count (1 or 2)
                outs_expr = (
                    ip_col.floor() * 3
                    + ((ip_col - ip_col.floor()) * 10).round(0)
                )
                points_expr = points_expr + (outs_expr * point_value)
            continue

        # --- Standard pitching stats ---
        # Check if the column exists in our DataFrame
        if col_name in df.columns:
            points_expr = points_expr + (
                pl.col(col_name).cast(pl.Float64).fill_null(0.0) * point_value
            )

    return df.with_columns(
        points_expr.round(1).alias("fantasy_pts")
    )
