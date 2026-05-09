"""
park_factors.py - Live MLB Park Factors with Baseball Savant Fetch + Static Fallback
======================================================================================

Park factors quantify how hitter-friendly or pitcher-friendly a stadium is
relative to the league average. They are an essential input for betting and
fantasy analysis — the same hitter producing the same OPS in Coors Field
versus Oracle Park is producing very different "true" performance because of
the venues.

Scale (industry standard):
    100 = league-neutral
    > 100 = hitter-friendly (boosts run scoring)
    < 100 = pitcher-friendly (suppresses run scoring)

Data source — Baseball Savant (public, free, official MLB Statcast):
    https://baseballsavant.mlb.com/leaderboard/statcast-park-factors

    The page embeds a `data = [{...}, ...]` JS variable with one row per venue.
    Each row carries:
        venue_name, name_display_club (team), index_runs, index_hr, year_range,
        plus split metrics like wOBA, OBP, K%, BB%, etc.

    We default to year=2026 which gives a 3-year rolling window (2024-2026).
    Single-year park factors are noisy due to small sample size; the rolling
    window is the industry-standard smoothing approach. Baseball Savant
    publishes these factors live and updates them as the season progresses.

Why this matters:
    The Athletics relocated to Sutter Health Park starting 2025; the Rays played
    2025 at Steinbrenner Field while Tropicana Field was repaired. Hardcoded
    "2024 estimates" would silently use the wrong venues for both teams.
    Pulling live from Baseball Savant means we always reflect the current
    venue + factor, no annual code update required.

Fallback:
    Two scenarios fall back to STATIC_PARK_FACTORS below:
        1. The Baseball Savant fetch fails (network error, page restructure,
           rate-limit). The cache stays populated with static values and the
           server keeps serving park factors.
        2. A specific venue is missing from the Savant response. New /
           temporary stadiums sometimes don't have enough rolling data to
           appear; the static lookup fills those gaps with placeholder values.

Refresh strategy:
    - On FastAPI startup, an async task kicks off `refresh_park_factors_cache()`.
      The cache starts pre-populated with STATIC_PARK_FACTORS so requests during
      the in-flight fetch get *something* coherent.
    - Render free tier spins the server down after 15 min of idle and cold-starts
      on the next request — so refreshes happen organically several times per
      day. We don't add a separate periodic refresh; the cold-start cadence is
      enough for slow-moving park-factor data.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# BASEBALL SAVANT FETCH CONFIG
# ---------------------------------------------------------------------------
# Savant blocks default Python/urllib User-Agents (returns 403). Sending a
# browser-shaped UA is the standard convention for tools like this; we
# identify ourselves so they can blocklist us cleanly if they ever object.
SAVANT_URL_TEMPLATE = (
    "https://baseballsavant.mlb.com/leaderboard/statcast-park-factors"
    "?type=year&year={year}"
)
SAVANT_USER_AGENT = (
    "Mozilla/5.0 (compatible; MLBFantasyStats/1.0; "
    "+https://github.com/BrianRhodes2018/MLBFantasyStats)"
)


# ---------------------------------------------------------------------------
# STATIC FALLBACK
# ---------------------------------------------------------------------------
# Used only when:
#   1. The Baseball Savant fetch fails entirely (no live data available)
#   2. A specific venue is missing from Savant data — typically new or
#      temporary stadiums whose 3-year rolling window doesn't yet have enough
#      sample size (e.g. Sutter Health Park 2025-onward, Steinbrenner Field
#      while the Rays were displaced).
#
# Approximate values from publicly published 2023-2024 averages
# (ESPN / Baseball Savant / FanGraphs). Refresh if Baseball Savant becomes
# permanently unreachable, otherwise these are just lifeboats.
STATIC_PARK_FACTORS: dict[str, dict] = {
    # Hitter-friendly
    "Coors Field":               {"runs": 117, "hr": 115, "team": "Rockies"},
    "Great American Ball Park":  {"runs": 110, "hr": 118, "team": "Reds"},
    "Globe Life Field":          {"runs": 106, "hr": 105, "team": "Rangers"},
    "Citizens Bank Park":        {"runs": 105, "hr": 110, "team": "Phillies"},
    "Fenway Park":               {"runs": 107, "hr": 99,  "team": "Red Sox"},
    "Yankee Stadium":            {"runs": 104, "hr": 113, "team": "Yankees"},
    "Chase Field":               {"runs": 104, "hr": 103, "team": "Diamondbacks"},
    # Mildly hitter-friendly
    "Wrigley Field":             {"runs": 102, "hr": 103, "team": "Cubs"},
    "Rate Field":                {"runs": 101, "hr": 105, "team": "White Sox"},
    "Guaranteed Rate Field":     {"runs": 101, "hr": 105, "team": "White Sox"},
    "Rogers Centre":             {"runs": 101, "hr": 104, "team": "Blue Jays"},
    # Neutral
    "Truist Park":               {"runs": 100, "hr": 100, "team": "Braves"},
    "Target Field":              {"runs": 100, "hr": 97,  "team": "Twins"},
    "Progressive Field":         {"runs": 100, "hr": 99,  "team": "Guardians"},
    "Angel Stadium":             {"runs": 100, "hr": 99,  "team": "Angels"},
    "Nationals Park":            {"runs": 100, "hr": 99,  "team": "Nationals"},
    "Comerica Park":             {"runs": 99,  "hr": 94,  "team": "Tigers"},
    "Camden Yards":              {"runs": 99,  "hr": 96,  "team": "Orioles"},
    "Oriole Park at Camden Yards": {"runs": 99, "hr": 96, "team": "Orioles"},
    "Kauffman Stadium":          {"runs": 99,  "hr": 94,  "team": "Royals"},
    "Busch Stadium":              {"runs": 98, "hr": 95,  "team": "Cardinals"},
    "Minute Maid Park":           {"runs": 98, "hr": 97,  "team": "Astros"},
    "Daikin Park":                {"runs": 98, "hr": 97,  "team": "Astros"},
    "American Family Field":      {"runs": 99, "hr": 104, "team": "Brewers"},
    "Dodger Stadium":             {"runs": 98, "hr": 104, "team": "Dodgers"},
    # Pitcher-friendly
    "PNC Park":                   {"runs": 97, "hr": 95,  "team": "Pirates"},
    "loanDepot park":             {"runs": 96, "hr": 88,  "team": "Marlins"},
    "Citi Field":                 {"runs": 95, "hr": 93,  "team": "Mets"},
    "Petco Park":                 {"runs": 94, "hr": 94,  "team": "Padres"},
    "Tropicana Field":            {"runs": 94, "hr": 95,  "team": "Rays"},
    "T-Mobile Park":              {"runs": 93, "hr": 93,  "team": "Mariners"},
    "Oracle Park":                {"runs": 94, "hr": 88,  "team": "Giants"},
    # New / relocated venues — placeholders until Baseball Savant accumulates enough data
    "Sutter Health Park":         {"runs": 100, "hr": 100, "team": "Athletics"},
    "Steinbrenner Field":         {"runs": 100, "hr": 102, "team": "Rays"},
}


# ---------------------------------------------------------------------------
# IN-MEMORY CACHE
# ---------------------------------------------------------------------------
# Pre-populated with the static fallback so the very first requests after
# a cold start get *something* — the in-flight Savant fetch then overwrites
# this with current data once it lands.
_CACHE: dict = {
    "factors": dict(STATIC_PARK_FACTORS),
    "year_range": "static fallback (2023-2024 estimates)",
    "source": "static_fallback",
    "fetched_at": None,
}


def _parse_savant_html(html: str) -> tuple[dict, Optional[str]]:
    """
    Extract park factors from a Baseball Savant leaderboard page.

    The page embeds the leaderboard data inline as a JS variable assignment:
        data = [{"venue_name":"Petco Park","index_runs":"94", ...}, ...];

    We pull that out with a regex (anchored at `data = [` and ending at the
    first `]` followed by a separator), JSON-parse it, and project down to
    the fields we actually care about.

    Args:
        html: The raw HTML of the Savant park-factors leaderboard page.

    Returns:
        (factors_dict, year_range_str)
        factors_dict is keyed by venue_name; each value has runs, hr, team, venue_id.
        year_range_str is e.g. "2024-2026" (the rolling 3-year window).

    Raises:
        ValueError if the embedded data block can't be located or parsed.
    """
    match = re.search(r"data\s*=\s*(\[\{.*?\}\])\s*[,;]", html, re.DOTALL)
    if not match:
        raise ValueError("Could not locate park-factor data block in Savant HTML")

    try:
        items = json.loads(match.group(1))
    except json.JSONDecodeError as e:
        raise ValueError(f"Park-factor data block was not valid JSON: {e}") from e

    factors: dict = {}
    year_range: Optional[str] = None
    for item in items:
        venue = item.get("venue_name")
        if not venue:
            continue
        try:
            factors[venue] = {
                "runs": int(item["index_runs"]),
                "hr": int(item["index_hr"]),
                "team": item.get("name_display_club", ""),
                "venue_id": int(item.get("venue_id", 0)),
            }
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Skipping malformed Savant row for '{venue}': {e}")
            continue
        # Every row carries the same year_range; capture it once.
        year_range = item.get("year_range") or year_range

    if not factors:
        raise ValueError("Savant data block parsed but yielded zero venues")

    return factors, year_range


async def refresh_park_factors_cache(year: Optional[int] = None) -> bool:
    """
    Fetch the latest park factors from Baseball Savant and update _CACHE.

    On success: _CACHE is replaced with merged Savant + static-fallback data.
    Static fallback values fill in for any venue Savant doesn't return
    (typically new/temporary stadiums with too little rolling-window sample).

    On failure: _CACHE is left as-is. If this is the first attempt it remains
    pre-populated with STATIC_PARK_FACTORS, so requests still get sensible
    output. Subsequent attempts (e.g. on the next cold start) will retry.

    Args:
        year: Year to query Savant for. Defaults to the current calendar year.
              The Savant 3-year rolling window ending at this year is what's
              returned (e.g. year=2026 -> 2024-2026).

    Returns:
        True if the live fetch succeeded and the cache was updated; False if
        we fell back (cache unchanged or filled with static defaults).
    """
    if year is None:
        year = datetime.now().year

    url = SAVANT_URL_TEMPLATE.format(year=year)
    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            headers={"User-Agent": SAVANT_USER_AGENT},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            factors, year_range = _parse_savant_html(response.text)

        # Fill any venues Savant didn't return with static fallback values.
        # This handles new/temporary stadiums (e.g. Sutter Health Park) that
        # don't have enough rolling-window data to appear in Savant yet.
        merged = dict(factors)
        filled_from_static = []
        for venue, data in STATIC_PARK_FACTORS.items():
            if venue not in merged:
                merged[venue] = dict(data)
                filled_from_static.append(venue)

        _CACHE["factors"] = merged
        _CACHE["year_range"] = year_range or str(year)
        _CACHE["source"] = "baseball_savant"
        _CACHE["fetched_at"] = datetime.now(timezone.utc).isoformat()

        logger.info(
            f"Park factors refreshed from Baseball Savant "
            f"(year_range={year_range}, "
            f"savant_venues={len(factors)}, "
            f"static_filled={len(filled_from_static)})"
        )
        return True

    except Exception as e:
        logger.warning(
            f"Failed to refresh park factors from Baseball Savant: {e}; "
            f"keeping {_CACHE['source']} data"
        )
        if _CACHE["fetched_at"] is None:
            # First attempt failed — record that we tried so /parks/factors
            # can show a useful timestamp.
            _CACHE["fetched_at"] = datetime.now(timezone.utc).isoformat()
        return False


def get_park_factor(venue_name: Optional[str]) -> Optional[dict]:
    """
    Look up the park factor for a given venue.

    Args:
        venue_name: Canonical venue name as returned by `statsapi.schedule(...)`
                    (e.g. "Yankee Stadium"). Pass None or empty string and you
                    get None back — callers should treat None as "no data" and
                    surface "—" rather than falsely showing 100.

    Returns:
        dict like {"runs": 104, "hr": 113, "team": "Yankees"} on hit, or None.
    """
    if not venue_name:
        return None
    return _CACHE["factors"].get(venue_name)


def get_all_factors_with_meta() -> dict:
    """
    Return the full cached park-factor mapping plus metadata about freshness.

    The metadata fields let the UI show "Park factors via Baseball Savant
    (2024-2026 rolling)" or "Static fallback — Savant unreachable" so users
    know how trustworthy / current the numbers are.
    """
    return {
        "factors": _CACHE["factors"],
        "year_range": _CACHE["year_range"],
        "source": _CACHE["source"],
        "fetched_at": _CACHE["fetched_at"],
        "venue_count": len(_CACHE["factors"]),
    }


def classify_park_factor(runs_factor: Optional[int]) -> str:
    """
    Coarse 3-bucket label for badge display.

    Thresholds match common baseball-analytics conventions:
        > 103   → hitter-friendly
        97-103  → neutral
        < 97    → pitcher-friendly
    """
    if runs_factor is None:
        return "unknown"
    if runs_factor > 103:
        return "hitter-friendly"
    if runs_factor < 97:
        return "pitcher-friendly"
    return "neutral"
