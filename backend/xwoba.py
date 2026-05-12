"""
xwoba.py - Baseball Savant Expected-Stats Scraper + Rolling-Window Helpers
============================================================================

Pulls per-hitter expected-stats data from Baseball Savant's public
leaderboard (same scrape pattern park_factors.py uses) and persists it as
daily snapshots in `hitter_savant_snapshots`. Once two weeks of snapshots
have accumulated for a hitter, we can derive true rolling-window stats by
subtracting an earlier snapshot from a later one — Savant only publishes
season aggregates, but the underlying counts are additive.

Why this module exists:
    The "Recent Form" signal in betting.py previously used rolling-14-day
    OPS divided by season OPS. The framework writeup the user shared
    flagged this as too noisy on its own — OPS is sloppy math (treats a
    walk like a single in OBP, weights a HR as exactly 4× a single in
    SLG), and "rate stat up" without process-stat confirmation is mostly
    BABIP luck. xwOBA + Barrel/PA + K% together fix both issues.

What we scrape (per qualified hitter):
    - pa                   — season PA count (subtraction-math denominator)
    - xwoba                — expected wOBA (primary form signal)
    - woba                 — actual wOBA (sanity / regression signal)
    - xba, xslg, ba, slg   — context (display only)
    - barrel_ct            — season barrel count (subtraction numerator)
    - hard_hit_ct          — season hard-hit count (subtraction numerator)
    - barrels_per_pa, hard_hit_percent, exit_velocity_avg — rates for display

What we don't scrape from Savant (because we already have it):
    - K%, BB% — derivable from batter_game_logs (K + BB + AB + HBP + SF
      are all per-game). Same with rolling versions.
    - rolling wOBA — also derivable from batter_game_logs using standard
      linear weights.

Scrape source:
    https://baseballsavant.mlb.com/leaderboard/expected_statistics?type=batter&year=YYYY
    Returns the same `data = [...]` inline JS pattern as the park-factors
    leaderboard. Same regex extraction approach.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SAVANT FETCH CONFIG
# ---------------------------------------------------------------------------
SAVANT_URL_TEMPLATE = (
    "https://baseballsavant.mlb.com/leaderboard/expected_statistics"
    "?type=batter&year={year}&minPA=q"
)
SAVANT_USER_AGENT = (
    "Mozilla/5.0 (compatible; MLBFantasyStats/1.0; "
    "+https://github.com/BrianRhodes2018/MLBFantasyStats)"
)


def _safe_float(v) -> Optional[float]:
    """Coerce Savant's string/numeric fields to float, returning None on
    junk or empty values. Savant sometimes returns "-" for null and
    sometimes the underlying number as a string."""
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _safe_int(v) -> Optional[int]:
    """Same coercion shape as _safe_float but for integer fields."""
    if v is None or v == "" or v == "-":
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        try:
            return int(float(v))
        except (ValueError, TypeError):
            return None


def parse_savant_expected_stats(html: str) -> list[dict]:
    """
    Extract per-hitter rows from a Baseball Savant expected-statistics page.

    The page embeds the leaderboard inline as a JS variable assignment:
        data = [{"entity_name":"Wood, James","entity_id":"695578","pa":"194", ...}, ...];

    We pull that block out, JSON-parse it, and project down to the fields
    `hitter_savant_snapshots` cares about. Rows missing entity_id or pa
    are skipped (they're useless without the join key + denominator).
    """
    match = re.search(r"data\s*=\s*(\[\{.*?\}\])\s*[,;]", html, re.DOTALL)
    if not match:
        raise ValueError("Could not locate expected-stats data block in Savant HTML")

    try:
        items = json.loads(match.group(1))
    except json.JSONDecodeError as e:
        raise ValueError(f"Savant data block was not valid JSON: {e}") from e

    parsed: list[dict] = []
    for item in items:
        player_mlb_id = _safe_int(item.get("entity_id"))
        pa = _safe_int(item.get("pa"))
        # Bail on rows missing the join key or denominator — they can't
        # contribute to either lookups or rolling math.
        if not player_mlb_id or pa is None or pa <= 0:
            continue

        parsed.append({
            "player_mlb_id": player_mlb_id,
            "pa": pa,
            "xwoba": _safe_float(item.get("est_woba")),
            "woba": _safe_float(item.get("woba")),
            "xba": _safe_float(item.get("est_ba")),
            "xslg": _safe_float(item.get("est_slg")),
            "ba": _safe_float(item.get("ba")),
            "slg": _safe_float(item.get("slg")),
            "barrel_ct": _safe_int(item.get("barrel_ct")),
            "hard_hit_ct": _safe_int(item.get("hard_hit_ct")),
            "barrels_per_pa": _safe_float(item.get("barrels_per_pa")),
            "hard_hit_percent": _safe_float(item.get("hard_hit_percent")),
            "exit_velocity_avg": _safe_float(item.get("exit_velocity_avg")),
        })

    if not parsed:
        raise ValueError("Savant data block parsed but yielded zero usable rows")

    return parsed


async def fetch_savant_expected_stats(year: Optional[int] = None) -> list[dict]:
    """
    Fetch and parse the current Savant expected-stats leaderboard for the
    given year. Returns a list of per-hitter dicts ready to be persisted
    as `hitter_savant_snapshots` rows.

    Args:
        year: MLB season year. Defaults to the current calendar year.

    Raises:
        httpx.HTTPError: network errors propagate
        ValueError: when the page format is unrecognized (Savant restructured
                    their HTML — caller should fall back to "skip this run"
                    rather than crash the whole daily update).
    """
    if year is None:
        year = datetime.now().year

    url = SAVANT_URL_TEMPLATE.format(year=year)
    async with httpx.AsyncClient(
        timeout=30.0,
        headers={"User-Agent": SAVANT_USER_AGENT},
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        return parse_savant_expected_stats(response.text)


# ---------------------------------------------------------------------------
# ROLLING WINDOW VIA SUBTRACTION MATH
# ---------------------------------------------------------------------------

def compute_rolling_from_snapshots(
    snapshots: list[dict],
    target_pas: int = 50,
) -> Optional[dict]:
    """
    Given a list of snapshots for one player ordered newest-first, derive
    a rolling-window stats dict covering roughly the last `target_pas`
    plate appearances.

    Algorithm:
        1. Newest snapshot is the "right edge" of the window.
        2. Walk backward until we find the first snapshot whose PA is at
           least `target_pas` smaller than the newest. That's the "left
           edge".
        3. The rolling values are (latest_count - earlier_count) over
           (latest_pa - earlier_pa).

    Returns None when:
        - fewer than 2 snapshots exist, OR
        - the earliest snapshot still hasn't dropped `target_pas` worth
          of PAs below the latest (i.e. we haven't accumulated enough
          history yet — typical situation for the first 1-2 weeks after
          we start scraping)

    The form signal calls this with target_pas=50 (middle of the
    framework's 40-60 PA window). 50 PAs is roughly 2 weeks for an
    everyday starter, 3-4 weeks for a platoon player.
    """
    if not snapshots or len(snapshots) < 2:
        return None

    latest = snapshots[0]
    if latest.get("pa") is None:
        return None

    pa_threshold = latest["pa"] - target_pas
    if pa_threshold <= 0:
        return None  # Player doesn't even have `target_pas` season PAs yet

    # Find the first snapshot at or below the threshold (walking from newest).
    earlier = None
    for snap in snapshots[1:]:
        if snap.get("pa") is None:
            continue
        if snap["pa"] <= pa_threshold:
            earlier = snap
            break

    if earlier is None:
        # We don't have a snapshot old enough yet. Common in the first
        # weeks after the daily scrape started.
        return None

    window_pa = latest["pa"] - earlier["pa"]
    if window_pa <= 0:
        return None  # Defensive — shouldn't happen given the threshold check

    def _delta_rate(count_field: str) -> Optional[float]:
        """Difference in counts divided by difference in PA, as a percentage."""
        c_late = latest.get(count_field)
        c_early = earlier.get(count_field)
        if c_late is None or c_early is None:
            return None
        return (c_late - c_early) / window_pa * 100.0

    def _delta_weighted(rate_field: str) -> Optional[float]:
        """
        For rate stats like xwOBA where we don't have a clean count, do
        weighted subtraction:
            rolling_rate = (rate_late × PA_late − rate_early × PA_early)
                         / (PA_late − PA_early)
        Rounds to 3 decimals to match Savant's published precision.
        """
        r_late = latest.get(rate_field)
        r_early = earlier.get(rate_field)
        if r_late is None or r_early is None:
            return None
        numerator = (r_late * latest["pa"]) - (r_early * earlier["pa"])
        return round(numerator / window_pa, 3)

    return {
        "window_pa": window_pa,
        "from_date": earlier.get("snapshot_date"),
        "to_date": latest.get("snapshot_date"),
        "xwoba": _delta_weighted("xwoba"),
        "woba": _delta_weighted("woba"),
        "barrels_per_pa": _delta_rate("barrel_ct"),
        "hard_hit_percent": _delta_rate("hard_hit_ct"),
    }


# ---------------------------------------------------------------------------
# IN-MEMORY CACHE FOR THE LATEST SAVANT SNAPSHOT
# ---------------------------------------------------------------------------
# The /betting/candidates orchestration needs season-level Savant data for
# every batter in today's lineups. Rather than hammering the DB with one
# query per batter, the daily-update phase populates this dict at write
# time and the betting endpoint reads from it directly.
#
# If the dict is empty (cold server start before a daily-update has run),
# the betting endpoint falls back to querying the snapshots table itself.

_latest_by_player: dict[int, dict] = {}
_latest_loaded_at: Optional[str] = None


def cache_latest_snapshots(snapshots: list[dict]) -> None:
    """Replace the in-memory `latest` cache with the given list of
    season-level snapshot rows."""
    global _latest_by_player, _latest_loaded_at
    _latest_by_player = {s["player_mlb_id"]: s for s in snapshots if s.get("player_mlb_id")}
    _latest_loaded_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_latest_snapshot(player_mlb_id: int) -> Optional[dict]:
    """Returns the cached season-level snapshot dict for a player, or None
    when we don't have data for them (player isn't qualified, or scrape
    hasn't run yet on a cold start)."""
    return _latest_by_player.get(player_mlb_id)


def get_cache_meta() -> dict:
    """Diagnostic — used by /system endpoints if we want to expose scrape
    freshness. Returns the load timestamp and a row count."""
    return {
        "loaded_at": _latest_loaded_at,
        "player_count": len(_latest_by_player),
    }


async def warm_cache_from_db(database, hitter_savant_snapshots_table) -> int:
    """
    Populate the in-memory cache from the most recent snapshot per player
    stored in the database. Called on FastAPI startup so the betting
    endpoint has season-level Savant data immediately after a cold start
    (Render free tier spins servers down after 15 min idle, then the next
    request triggers a fresh boot — without this hook the betting page's
    first generation after each spin-up would be missing Savant gates).

    Idempotent: replaces the cache wholesale. Safe to call multiple times.

    Args:
        database:                            the async Database instance
        hitter_savant_snapshots_table:       the SQLAlchemy Table object
                                             (passed in to avoid circular
                                             imports between xwoba.py and
                                             models.py callers)

    Returns:
        Count of distinct players loaded into the cache.
    """
    # Pull every row; in Python we keep just the newest per player. With
    # ~250 hitters × ~180 days/season = ~45K rows max, this is fast.
    rows = await database.fetch_all(
        hitter_savant_snapshots_table.select().order_by(
            hitter_savant_snapshots_table.c.snapshot_date.desc()
        )
    )
    latest_by_player: dict[int, dict] = {}
    for r in rows:
        m = dict(r._mapping)
        pid = m.get("player_mlb_id")
        if pid is None or pid in latest_by_player:
            continue
        latest_by_player[pid] = m
    cache_latest_snapshots(list(latest_by_player.values()))
    logger.info(f"Savant cache warmed from DB: {len(latest_by_player)} players")
    return len(latest_by_player)
