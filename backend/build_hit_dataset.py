"""
build_hit_dataset.py - Build a model-training dataset for daily 1+ hit prediction.

Phase 1 of the hit-prediction model plan: turn the season's game logs and
historical boxscores into a flat table with ONE ROW PER BATTER PER GAME.
Every feature on a row is computed strictly from data dated BEFORE that
game (no leakage), and the label is whether the batter recorded 1+ hit
in that game.

Where the data comes from:
  - MLB StatsAPI boxscores (cached as JSON on disk): the historical
    lineups, batting orders, starting pitchers, venues, and game outcomes.
    Batter and pitcher form histories are ALSO accumulated from these
    boxscores as days are processed chronologically, because the DB's
    game-log tables only cover the app's tracked players (~45% of
    league-wide batter-games). Boxscores cover everyone.
    The cache directory is shared with outcome_backtest.py, so previously
    fetched games are never re-downloaded.
  - Postgres (players / pitchers): batter handedness (bats) and pitcher
    handedness (throws), with the boxscore feed as fallback.

Because form history is accumulated in-range, the build should start at
the season's first game date — otherwise early rows see artificially
thin history.

The output is a Parquet file (plus a printed summary). Rows are NOT
filtered by sample-size thresholds — columns like `season_pa` and
`p_season_ip` are included so that training code can choose its own
eligibility filters.

Example:
    python backend/build_hit_dataset.py \
        --start-date 2026-03-25 --end-date 2026-07-03 \
        --cache-dir backend/.backtest_cache \
        --output backend/data/hit_dataset.parquet
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

import polars as pl
import statsapi
from databases import Database
from dotenv import load_dotenv

from database import normalize_database_url
from park_factors import get_park_factor


BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_CACHE_DIR = BACKEND_DIR / ".backtest_cache"
DEFAULT_OUTPUT = BACKEND_DIR / "data" / "hit_dataset.parquet"

# Game-count windows for recent batter form. Games (not calendar days) keep
# the sample size consistent for players who sat out or had off days.
FORM_WINDOWS = (5, 10, 20)

# Standard linear-weights wOBA coefficients (matches betting/analysis code).
_WOBA_W_BB = 0.69
_WOBA_W_HBP = 0.72
_WOBA_W_1B = 0.88
_WOBA_W_2B = 1.25
_WOBA_W_3B = 1.58
_WOBA_W_HR = 2.02

# Boxscore game statuses that count as a completed game.
_FINAL_STATUSES = {"final", "game over", "completed early"}


# ---------------------------------------------------------------------------
# Small parsing helpers
# ---------------------------------------------------------------------------

def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "" or value == "-":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def date_range(start: date, end: date) -> list[date]:
    days = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def log_row_pa(row: Mapping[str, Any]) -> int:
    """Plate appearances from a batter game line (AB + BB + HBP + SF)."""
    return (
        safe_int(row.get("at_bats"))
        + safe_int(row.get("walks"))
        + safe_int(row.get("hit_by_pitch"))
        + safe_int(row.get("sacrifice_flies"))
    )


def batting_line_from_boxscore(batting: Mapping[str, Any]) -> dict[str, int]:
    """
    Convert a boxscore `stats.batting` blob into the same shape as a
    batter_game_logs row, so one aggregation code path serves both.
    """
    return {
        "at_bats": safe_int(batting.get("atBats")),
        "hits": safe_int(batting.get("hits")),
        "doubles": safe_int(batting.get("doubles")),
        "triples": safe_int(batting.get("triples")),
        "home_runs": safe_int(batting.get("homeRuns")),
        "walks": safe_int(batting.get("baseOnBalls")),
        "hit_by_pitch": safe_int(batting.get("hitByPitch")),
        "sacrifice_flies": safe_int(batting.get("sacFlies")),
        "strikeouts": safe_int(batting.get("strikeOuts")),
    }


def pitching_line_from_boxscore(pitching: Mapping[str, Any]) -> dict[str, Any]:
    """
    Convert a boxscore `stats.pitching` blob into a pitcher game line.

    Innings pitched come from `outs / 3` — exact thirds, avoiding the
    "6.2 means 6 and two-thirds" string convention. `battersFaced` is
    kept so rate stats can use true PA instead of an estimate.
    """
    return {
        "innings_pitched": safe_int(pitching.get("outs")) / 3.0,
        "hits_allowed": safe_int(pitching.get("hits")),
        "earned_runs": safe_int(pitching.get("earnedRuns")),
        "walks": safe_int(pitching.get("baseOnBalls")),
        "strikeouts": safe_int(pitching.get("strikeOuts")),
        "home_runs_allowed": safe_int(pitching.get("homeRuns")),
        "hit_by_pitch": safe_int(pitching.get("hitBatsmen")),
        "batters_faced": safe_int(pitching.get("battersFaced")),
        "started": safe_int(pitching.get("gamesStarted")) > 0,
    }


# ---------------------------------------------------------------------------
# Batter aggregation (pure functions — unit tested)
# ---------------------------------------------------------------------------

def batter_window_stats(rows: Iterable[Mapping[str, Any]]) -> dict[str, Optional[float]]:
    """
    Aggregate a set of batter game-log rows into rate stats.

    Returns pa plus hit_per_pa, k_pct, contact_rate ((AB - K) / PA — how
    often the batter puts the ball in play or reaches without striking
    out), and woba. Rates are None when PA is 0 so that missing sample
    reads as "unknown", never as "bad".
    """
    pa = ab = hits = doubles = triples = homers = walks = hbp = sf = ks = 0
    for row in rows:
        pa += log_row_pa(row)
        ab += safe_int(row.get("at_bats"))
        hits += safe_int(row.get("hits"))
        doubles += safe_int(row.get("doubles"))
        triples += safe_int(row.get("triples"))
        homers += safe_int(row.get("home_runs"))
        walks += safe_int(row.get("walks"))
        hbp += safe_int(row.get("hit_by_pitch"))
        sf += safe_int(row.get("sacrifice_flies"))
        ks += safe_int(row.get("strikeouts"))

    if pa <= 0:
        return {"pa": 0, "hit_per_pa": None, "k_pct": None, "contact_rate": None, "woba": None}

    singles = max(hits - doubles - triples - homers, 0)
    woba_denom = ab + walks + hbp + sf
    woba = None
    if woba_denom > 0:
        woba = (
            _WOBA_W_BB * walks
            + _WOBA_W_HBP * hbp
            + _WOBA_W_1B * singles
            + _WOBA_W_2B * doubles
            + _WOBA_W_3B * triples
            + _WOBA_W_HR * homers
        ) / woba_denom

    return {
        "pa": pa,
        "hit_per_pa": hits / pa,
        "k_pct": ks / pa * 100.0,
        "contact_rate": (ab - ks) / pa,
        "woba": woba,
    }


# ---------------------------------------------------------------------------
# Pitcher aggregation (pure function — unit tested)
# ---------------------------------------------------------------------------

def pitcher_agg(rows: Iterable[Mapping[str, Any]]) -> dict[str, Optional[float]]:
    """
    Aggregate pitcher game lines into pregame rate stats.

    PA against uses summed `batters_faced` when the lines carry it
    (boxscore-sourced lines do); otherwise it falls back to the estimate
    3*IP + H + BB + HBP — the same convention outcome_backtest.py uses.
    """
    ip = 0.0
    hits = er = walks = ks = hr = hbp = bf = 0
    for row in rows:
        ip += safe_float(row.get("innings_pitched"), 0.0) or 0.0
        hits += safe_int(row.get("hits_allowed"))
        er += safe_int(row.get("earned_runs"))
        walks += safe_int(row.get("walks"))
        ks += safe_int(row.get("strikeouts"))
        hr += safe_int(row.get("home_runs_allowed"))
        hbp += safe_int(row.get("hit_by_pitch"))
        bf += safe_int(row.get("batters_faced"))

    if ip <= 0:
        return {
            "ip": 0.0, "h_per_9": None, "whip": None, "fip": None,
            "k_pct": None, "bb_pct": None, "k_bb_pct": None, "hr_per_9": None,
        }

    pa_est = bf if bf > 0 else (3 * ip) + hits + walks + hbp
    fip = ((13 * hr) + (3 * (walks + hbp)) - (2 * ks)) / ip + 3.15
    return {
        "ip": ip,
        "h_per_9": hits / ip * 9.0,
        "whip": (hits + walks) / ip,
        "fip": fip,
        "k_pct": (ks / pa_est * 100.0) if pa_est > 0 else None,
        "bb_pct": (walks / pa_est * 100.0) if pa_est > 0 else None,
        "k_bb_pct": ((ks - walks) / pa_est * 100.0) if pa_est > 0 else None,
        "hr_per_9": hr / ip * 9.0,
    }


# ---------------------------------------------------------------------------
# Platoon helpers (pure functions — unit tested)
# ---------------------------------------------------------------------------

def platoon_advantage(bats: Optional[str], throws: Optional[str]) -> Optional[int]:
    """
    1 = batter has the platoon edge (opposite hands, or switch hitter),
    0 = same-handed matchup, None = handedness unknown on either side.
    """
    if not bats or not throws:
        return None
    bats = bats.upper()
    throws = throws.upper()
    if bats == "S":
        return 1
    return 1 if bats != throws else 0


class RateHistory:
    """
    Accumulates hits/PA totals under an arbitrary hashable key, built
    incrementally as backtest days are processed. Used for two splits:
      - (batter_id, starter_hand): platoon-side hit rate
      - (batter_id, starter_id):   season batter-vs-pitcher hit rate

    Approximation note: a batter's full-game line includes plate
    appearances against relievers, but all of it gets credited to the
    starter. That is the standard free-data compromise — the starter
    faces the batter first and most often.

    Call `snapshot(key)` BEFORE adding the current day's outcomes so
    features never see same-day results. A key of None (e.g. unknown
    handedness) reads and records nothing.
    """

    def __init__(self) -> None:
        self._totals: dict[Any, list[int]] = {}

    def snapshot(self, key: Any) -> dict[str, Optional[float]]:
        if key is None:
            return {"pa": 0, "hit_per_pa": None}
        hits, pa = self._totals.get(key, (0, 0))
        return {"pa": pa, "hit_per_pa": (hits / pa) if pa > 0 else None}

    def add(self, key: Any, hits: int, pa: int) -> None:
        if key is None or pa <= 0:
            return
        current = self._totals.setdefault(key, [0, 0])
        current[0] += hits
        current[1] += pa


def hand_key(player_id: int, hand: Optional[str]) -> Optional[tuple[int, str]]:
    """RateHistory key for a batter's split vs a pitcher hand."""
    return (player_id, hand.upper()) if hand else None


# ---------------------------------------------------------------------------
# StatsAPI fetch layer with the shared on-disk cache
# ---------------------------------------------------------------------------

class BoxscoreSource:
    """Fetches schedules and game feeds, caching JSON to disk permanently.

    Uses the same cache filenames as outcome_backtest.py
    (schedule_YYYY-MM-DD.json / game_<gamePk>.json) so both tools share
    one cache directory and nothing is downloaded twice.

    New fetches are written gzip-compressed (game feeds are 1-2 MB of
    JSON each; multi-season pulls would otherwise take 10+ GB). Reads
    accept either form, so the pre-existing uncompressed cache from
    earlier runs keeps working untouched.
    """

    def __init__(self, cache_dir: Path, *, request_delay_seconds: float = 0.08) -> None:
        self.cache_dir = cache_dir
        self.request_delay_seconds = request_delay_seconds

    def _cached_fetch(self, cache_name: str, fetch, *, refresh: bool = False) -> Optional[Any]:
        """Read-through cache. `refresh=True` skips the cache READ (the
        result is still written), for data that may have been cached in a
        non-final state — e.g. a schedule cached before that day's games
        finished, which would otherwise be stale forever."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        plain_path = self.cache_dir / cache_name
        gz_path = self.cache_dir / f"{cache_name}.gz"
        if not refresh:
            if plain_path.exists():
                return json.loads(plain_path.read_text(encoding="utf-8"))
            if gz_path.exists():
                return json.loads(gzip.decompress(gz_path.read_bytes()).decode("utf-8"))
        try:
            data = fetch()
        except Exception as exc:
            print(f"Warning: fetch failed for {cache_name}: {exc}")
            return None
        payload = json.dumps(data, sort_keys=True).encode("utf-8")
        gz_path.write_bytes(gzip.compress(payload, compresslevel=6))
        if refresh and plain_path.exists():
            # The gzip copy is now the authoritative one; drop the stale
            # plain file so reads don't resurrect it.
            plain_path.unlink()
        if self.request_delay_seconds:
            time.sleep(self.request_delay_seconds)
        return data

    def schedule(self, target: date, *, refresh: bool = False) -> list[dict[str, Any]]:
        stamp = target.strftime("%m/%d/%Y")
        data = self._cached_fetch(
            f"schedule_{target.isoformat()}.json",
            lambda: statsapi.schedule(start_date=stamp, end_date=stamp),
            refresh=refresh,
        )
        return data or []

    def game(self, game_id: int) -> Optional[dict[str, Any]]:
        return self._cached_fetch(
            f"game_{game_id}.json",
            lambda: statsapi.get("game", {"gamePk": game_id}),
        )

    def final_games(self, target: date, *, refresh_schedule: bool = False) -> list[dict[str, Any]]:
        games = []
        for entry in self.schedule(target, refresh=refresh_schedule):
            # Regular-season games only: historical March/October dates
            # include spring training ('S') and postseason games, which
            # have non-representative lineups and pitcher usage.
            if str(entry.get("game_type") or "R") != "R":
                continue
            if str(entry.get("status") or "").lower() not in _FINAL_STATUSES:
                continue
            game_id = safe_int(entry.get("game_id"))
            if not game_id:
                continue
            game_data = self.game(game_id)
            if game_data:
                games.append({"schedule": entry, "game": game_data})
        return games


# ---------------------------------------------------------------------------
# Dataset builder
# ---------------------------------------------------------------------------

class HitDatasetBuilder:
    def __init__(self, *, db: Database, source: BoxscoreSource) -> None:
        self.db = db
        self.source = source
        # Per-player game lines accumulated from boxscores as days are
        # processed in order. Only PRIOR days' lines are ever present when
        # a day's features are computed (updated at the end of each day).
        self.batter_history: dict[int, list[dict[str, Any]]] = {}
        self.pitcher_history: dict[int, list[dict[str, Any]]] = {}
        # Reliever (non-starter) lines grouped by team name, for
        # opposing-bullpen quality features.
        self.bullpen_history: dict[str, list[dict[str, Any]]] = {}
        self.bats_by_player: dict[int, Optional[str]] = {}
        self.throws_by_pitcher: dict[int, Optional[str]] = {}
        self.vs_hand = RateHistory()
        self.vs_pitcher = RateHistory()

    async def load_db_context(self) -> dict[str, Any]:
        player_rows = await self.db.fetch_all(
            "select mlb_id, bats from players where mlb_id is not null"
        )
        pitcher_identity_rows = await self.db.fetch_all(
            "select mlb_id, throws from pitchers where mlb_id is not null"
        )
        self.bats_by_player = {int(r["mlb_id"]): r["bats"] for r in player_rows}
        self.throws_by_pitcher = {int(r["mlb_id"]): r["throws"] for r in pitcher_identity_rows}
        return {
            "players_with_bats": len(self.bats_by_player),
            "pitchers_with_throws": len(self.throws_by_pitcher),
        }

    # -- pregame feature blocks ---------------------------------------------

    def batter_features(self, player_id: int, target: date) -> dict[str, Any]:
        """Season + rolling-window form from accumulated prior game lines,
        plus rest/fatigue context (days since last game, games in last 7)."""
        rows = self.batter_history.get(player_id, [])
        season = batter_window_stats(rows)
        features: dict[str, Any] = {
            "season_pa": season["pa"],
            "season_hit_per_pa": season["hit_per_pa"],
            "season_k_pct": season["k_pct"],
            "season_contact_rate": season["contact_rate"],
            "season_woba": season["woba"],
        }
        for window in FORM_WINDOWS:
            recent = batter_window_stats(rows[-window:])
            features[f"last{window}_pa"] = recent["pa"]
            features[f"last{window}_hit_per_pa"] = recent["hit_per_pa"]
            features[f"last{window}_k_pct"] = recent["k_pct"]
            features[f"last{window}_contact_rate"] = recent["contact_rate"]
            features[f"last{window}_woba"] = recent["woba"]

        if rows:
            last_game = parse_iso_date(rows[-1]["game_date"])
            features["days_rest"] = (target - last_game).days
        else:
            features["days_rest"] = None
        week_ago_iso = (target - timedelta(days=7)).isoformat()
        features["games_last7"] = sum(1 for r in rows if r["game_date"] >= week_ago_iso)
        return features

    def pitcher_features(self, pitcher_id: int) -> dict[str, Any]:
        """Opposing starter's season and last-3-outings rates from prior lines."""
        rows = self.pitcher_history.get(pitcher_id, [])
        season = pitcher_agg(rows)
        last3 = pitcher_agg(rows[-3:])
        features = {f"p_season_{key}": value for key, value in season.items()}
        features.update({f"p_last3_{key}": value for key, value in last3.items()})
        features["p_season_starts"] = sum(1 for r in rows if r.get("started"))
        return features

    def pregame_features(
        self,
        *,
        player_id: int,
        slot: int,
        is_home: bool,
        bats: Optional[str],
        throws: Optional[str],
        starter_id: int,
        park: Optional[Mapping[str, Any]],
        bullpen: Mapping[str, Optional[float]],
        pitcher_feats: Mapping[str, Any],
        target: date,
    ) -> dict[str, Any]:
        """
        The complete pre-game feature dict for one batter-game. Used by
        BOTH the historical dataset build and the daily prediction script,
        so training and serving can never drift apart.
        """
        vs_hand = self.vs_hand.snapshot(hand_key(player_id, throws))
        vs_pitcher = self.vs_pitcher.snapshot((player_id, starter_id))
        return {
            "batting_order": slot,
            "is_home": is_home,
            # -- platoon + season BvP
            "platoon_advantage": platoon_advantage(bats, throws),
            "vs_hand_pa": vs_hand["pa"],
            "vs_hand_hit_per_pa": vs_hand["hit_per_pa"],
            "faced_pitcher_pa": vs_pitcher["pa"],
            "faced_pitcher_hit_per_pa": vs_pitcher["hit_per_pa"],
            # -- park
            "park_runs_factor": (park or {}).get("runs"),
            "park_hr_factor": (park or {}).get("hr"),
            # -- opposing bullpen quality
            "opp_bullpen_ip": bullpen["ip"],
            "opp_bullpen_h_per_9": bullpen["h_per_9"],
            "opp_bullpen_whip": bullpen["whip"],
            "opp_bullpen_k_pct": bullpen["k_pct"],
            # -- batter form + pitcher blocks
            **self.batter_features(player_id, target),
            **pitcher_feats,
        }

    # -- per-day extraction ---------------------------------------------------

    def rows_for_date(self, target: date) -> list[dict[str, Any]]:
        target_iso = target.isoformat()
        day_rows: list[dict[str, Any]] = []
        # Everything observed today is collected here and applied to the
        # histories only AFTER the whole day is featurized — so features
        # never see same-day outcomes (doubleheaders included).
        day_outcomes: list[tuple[int, int, Optional[str], int, int]] = []
        day_batter_lines: list[tuple[int, dict[str, Any]]] = []
        day_pitcher_lines: list[tuple[int, dict[str, Any]]] = []
        day_bullpen_lines: list[tuple[str, dict[str, Any]]] = []

        for slate_game in self.source.final_games(target):
            schedule = slate_game["schedule"]
            game = slate_game["game"]
            game_data = game.get("gameData", {})
            game_players = game_data.get("players", {})
            box_teams = game.get("liveData", {}).get("boxscore", {}).get("teams", {})
            venue = schedule.get("venue_name") or game_data.get("venue", {}).get("name")
            park = get_park_factor(venue)

            # Collect every batting and pitching line in the game (both
            # teams, starters AND subs/relievers) for the form histories.
            for side in ("away", "home"):
                side_team = (box_teams.get(side, {}).get("team") or {}).get("name") or schedule.get(f"{side}_name")
                for box_player in (box_teams.get(side, {}).get("players") or {}).values():
                    pid = safe_int((box_player.get("person") or {}).get("id"))
                    if not pid:
                        continue
                    stats = box_player.get("stats", {})
                    batting = stats.get("batting") or {}
                    if safe_int(batting.get("plateAppearances")) > 0:
                        line = batting_line_from_boxscore(batting)
                        line["game_date"] = target_iso
                        day_batter_lines.append((pid, line))
                    pitching = stats.get("pitching") or {}
                    if safe_int(pitching.get("battersFaced")) > 0:
                        line = pitching_line_from_boxscore(pitching)
                        line["game_date"] = target_iso
                        day_pitcher_lines.append((pid, line))
                        if not line["started"] and side_team:
                            day_bullpen_lines.append((side_team, line))

            for offense_side in ("away", "home"):
                defense_side = "home" if offense_side == "away" else "away"
                offense = box_teams.get(offense_side, {})
                defense = box_teams.get(defense_side, {})
                batting_order = (offense.get("battingOrder") or [])[:9]
                if not batting_order:
                    continue

                pitcher_ids = defense.get("pitchers") or []
                starter_id = safe_int(pitcher_ids[0]) if pitcher_ids else 0
                if not starter_id:
                    probable = game_data.get("probablePitchers", {}).get(defense_side, {})
                    starter_id = safe_int(probable.get("id"))
                if not starter_id:
                    continue

                pitcher_person = game_players.get(f"ID{starter_id}", {})
                throws = (
                    self.throws_by_pitcher.get(starter_id)
                    or (pitcher_person.get("pitchHand") or {}).get("code")
                )
                pitcher_feats = self.pitcher_features(starter_id)

                team_name = offense.get("team", {}).get("name") or schedule.get(f"{offense_side}_name")
                opponent_name = defense.get("team", {}).get("name") or schedule.get(f"{defense_side}_name")

                # Opposing bullpen quality: ~40% of a batter's PAs come
                # against relievers, who are absent from starter metrics.
                bullpen = pitcher_agg(self.bullpen_history.get(opponent_name, []))

                for slot, raw_player_id in enumerate(batting_order, start=1):
                    player_id = safe_int(raw_player_id)
                    box_player = offense.get("players", {}).get(f"ID{player_id}", {})
                    game_player = game_players.get(f"ID{player_id}", {})
                    batting = box_player.get("stats", {}).get("batting", {})

                    hits_game = safe_int(batting.get("hits"))
                    pa_game = safe_int(batting.get("plateAppearances"))
                    bats = (
                        self.bats_by_player.get(player_id)
                        or (game_player.get("batSide") or {}).get("code")
                    )
                    day_outcomes.append((player_id, starter_id, throws, hits_game, pa_game))

                    day_rows.append({
                        # -- identifiers / context
                        "game_date": target_iso,
                        "game_id": safe_int(schedule.get("game_id")),
                        "player_id": player_id,
                        "player_name": (
                            (box_player.get("person") or {}).get("fullName")
                            or game_player.get("fullName")
                            or str(player_id)
                        ),
                        "team": team_name,
                        "opponent": opponent_name,
                        "venue": venue,
                        "bats": bats,
                        "pitcher_id": starter_id,
                        "pitcher_name": pitcher_person.get("fullName") or str(starter_id),
                        "pitcher_throws": throws,
                        # -- label + same-game outcome detail
                        "got_hit": hits_game >= 1,
                        "hits_game": hits_game,
                        "pa_game": pa_game,
                        "ab_game": safe_int(batting.get("atBats")),
                        "total_bases_game": safe_int(batting.get("totalBases")),
                        # -- everything the model sees
                        **self.pregame_features(
                            player_id=player_id,
                            slot=slot,
                            is_home=offense_side == "home",
                            bats=bats,
                            throws=throws,
                            starter_id=starter_id,
                            park=park,
                            bullpen=bullpen,
                            pitcher_feats=pitcher_feats,
                            target=target,
                        ),
                    })

        # Day is fully featurized — now fold today's results into history.
        for player_id, starter_id, hand, hits_game, pa_game in day_outcomes:
            self.vs_hand.add(hand_key(player_id, hand), hits_game, pa_game)
            self.vs_pitcher.add((player_id, starter_id), hits_game, pa_game)
        for player_id, line in day_batter_lines:
            self.batter_history.setdefault(player_id, []).append(line)
        for pitcher_id, line in day_pitcher_lines:
            self.pitcher_history.setdefault(pitcher_id, []).append(line)
        for team, line in day_bullpen_lines:
            self.bullpen_history.setdefault(team, []).append(line)
        return day_rows

    def build(self, start: date, end: date, *, verbose: bool = True) -> list[dict[str, Any]]:
        all_rows: list[dict[str, Any]] = []
        for target in date_range(start, end):
            day_rows = self.rows_for_date(target)
            all_rows.extend(day_rows)
            if verbose:
                print(f"{target.isoformat()}: batter-games={len(day_rows)}")
        return all_rows


# ---------------------------------------------------------------------------
# Output + summary
# ---------------------------------------------------------------------------

def summarize(df: pl.DataFrame) -> None:
    print("\nDATASET SUMMARY")
    print(f"rows: {df.height}")
    print(f"dates: {df['game_date'].min()} through {df['game_date'].max()}")
    print(f"unique batters: {df['player_id'].n_unique()}")
    print(f"unique pitchers: {df['pitcher_id'].n_unique()}")
    print(f"hit rate (all rows): {df['got_hit'].mean():.4f}")
    with_pa = df.filter(pl.col("pa_game") > 0)
    print(f"hit rate (rows with 1+ PA): {with_pa['got_hit'].mean():.4f}  ({with_pa.height} rows)")

    print("\nNULL RATES (key features)")
    key_columns = [
        "season_hit_per_pa", "last10_hit_per_pa", "last5_woba",
        "platoon_advantage", "vs_hand_hit_per_pa", "faced_pitcher_hit_per_pa",
        "p_season_h_per_9", "p_last3_whip", "park_runs_factor", "bats", "pitcher_throws",
        "opp_bullpen_h_per_9", "days_rest",
    ]
    for column in key_columns:
        null_rate = df[column].null_count() / df.height if df.height else 0.0
        print(f"  {column}: {null_rate:.3f}")


async def run(args: argparse.Namespace) -> int:
    env_path = BACKEND_DIR / ".env"
    if env_path.exists():
        load_dotenv(env_path)

    raw_url = os.environ.get("DATABASE_URL")
    if not raw_url:
        raise RuntimeError("DATABASE_URL is not set. Add it to backend/.env or your shell.")
    async_url, _ = normalize_database_url(raw_url)
    db = Database(async_url)
    await db.connect()

    try:
        if args.end_date:
            end = parse_iso_date(args.end_date)
        else:
            row = await db.fetch_one("select max(game_date) as max_date from batter_game_logs")
            if row is None or row["max_date"] is None:
                raise RuntimeError("No batter_game_logs rows found; cannot determine end date.")
            end = parse_iso_date(row["max_date"])
        if args.start_date:
            start = parse_iso_date(args.start_date)
        else:
            row = await db.fetch_one("select min(game_date) as min_date from batter_game_logs")
            start = parse_iso_date(row["min_date"])

        source = BoxscoreSource(
            Path(args.cache_dir),
            request_delay_seconds=args.request_delay_seconds,
        )
        builder = HitDatasetBuilder(db=db, source=source)
        db_meta = await builder.load_db_context()

        print("HIT DATASET BUILD")
        print(f"Dates: {start.isoformat()} through {end.isoformat()}")
        print(f"DB context: {db_meta}")
        print(f"Cache dir: {args.cache_dir}")
        print()

        rows = builder.build(start, end)
        if not rows:
            raise RuntimeError("No rows produced — check date range and cache/API access.")

        df = pl.DataFrame(rows, infer_schema_length=None)
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(output_path)
        summarize(df)
        print(f"\nSaved: {output_path} ({output_path.stat().st_size / 1_048_576:.1f} MB)")
        return 0
    finally:
        await db.disconnect()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the per-batter-game training dataset for hit prediction.",
    )
    parser.add_argument("--start-date", help="Inclusive YYYY-MM-DD start. Defaults to earliest batter game log.")
    parser.add_argument("--end-date", help="Inclusive YYYY-MM-DD end. Defaults to latest batter game log.")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR), help="Shared MLB StatsAPI JSON cache directory.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output Parquet path.")
    parser.add_argument("--request-delay-seconds", type=float, default=0.08, help="Delay between uncached StatsAPI calls.")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
