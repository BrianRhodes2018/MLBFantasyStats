"""
outcome_backtest.py - Historical hitter outcome backtests for betting weights.

This command rebuilds historical MLB slates from MLB StatsAPI boxscores,
scores each lineup hitter with several candidate betting-weight configs, and
compares those picks to actual same-game outcomes.

It is intentionally an outcome backtest, not a sportsbook ROI backtest:
we do not have historical odds snapshots yet, so the outputs focus on hit
rate, 2+ total bases rate, HR rate, average total bases, and bust rate.

Example:
    python backend/outcome_backtest.py --days 30 --top-n-per-day 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from databases import Database
from dotenv import load_dotenv

import statsapi

from betting import (
    score_bvp,
    score_pitcher_vulnerability,
    score_platoon,
    score_recent_form,
)
from database import normalize_database_url
from park_factors import get_park_factor


BACKEND_DIR = Path(__file__).resolve().parent
ROOT_DIR = BACKEND_DIR.parent
DEFAULT_CACHE_DIR = BACKEND_DIR / ".backtest_cache"
DEFAULT_RESULTS_DIR = BACKEND_DIR / "backtest_results"

_WOBA_W_BB = 0.69
_WOBA_W_HBP = 0.72
_WOBA_W_1B = 0.88
_WOBA_W_2B = 1.25
_WOBA_W_3B = 1.58
_WOBA_W_HR = 2.02


@dataclass(frozen=True)
class WeightConfig:
    name: str
    platoon: float
    pitcher: float
    form: float
    bvp: float
    note: str


DEFAULT_WEIGHT_CONFIGS = [
    WeightConfig(
        "current",
        platoon=0.30,
        pitcher=0.30,
        form=0.20,
        bvp=0.20,
        note="Current app weights; BvP is neutral when unavailable.",
    ),
    WeightConfig(
        "form_boost",
        platoon=0.25,
        pitcher=0.25,
        form=0.35,
        bvp=0.15,
        note="Moderate recent-form increase.",
    ),
    WeightConfig(
        "aggressive_form",
        platoon=0.20,
        pitcher=0.25,
        form=0.40,
        bvp=0.15,
        note="More aggressive streak/form setup.",
    ),
    WeightConfig(
        "no_bvp_form",
        platoon=0.25,
        pitcher=0.30,
        form=0.45,
        bvp=0.00,
        note="Redistributes BvP to form and pitcher context.",
    ),
    WeightConfig(
        "balanced_no_bvp",
        platoon=0.30,
        pitcher=0.30,
        form=0.40,
        bvp=0.00,
        note="No BvP; balanced matchup and form emphasis.",
    ),
]


def parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def date_range(start: date, end: date) -> list[date]:
    days = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


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


def plate_appearances(row: Mapping[str, Any]) -> int:
    return (
        safe_int(row.get("at_bats"))
        + safe_int(row.get("walks"))
        + safe_int(row.get("hit_by_pitch"))
        + safe_int(row.get("sacrifice_flies"))
    )


def woba_from_logs(rows: Iterable[Mapping[str, Any]]) -> dict[str, Optional[float]]:
    totals = {
        "pa": 0,
        "ab": 0,
        "h": 0,
        "double": 0,
        "triple": 0,
        "hr": 0,
        "bb": 0,
        "hbp": 0,
        "sf": 0,
        "k": 0,
    }
    for row in rows:
        hits = safe_int(row.get("hits"))
        doubles = safe_int(row.get("doubles"))
        triples = safe_int(row.get("triples"))
        homers = safe_int(row.get("home_runs"))
        walks = safe_int(row.get("walks"))
        hbp = safe_int(row.get("hit_by_pitch"))
        sf = safe_int(row.get("sacrifice_flies"))
        ab = safe_int(row.get("at_bats"))
        totals["pa"] += ab + walks + hbp + sf
        totals["ab"] += ab
        totals["h"] += hits
        totals["double"] += doubles
        totals["triple"] += triples
        totals["hr"] += homers
        totals["bb"] += walks
        totals["hbp"] += hbp
        totals["sf"] += sf
        totals["k"] += safe_int(row.get("strikeouts"))

    singles = max(
        totals["h"] - totals["double"] - totals["triple"] - totals["hr"],
        0,
    )
    denom = totals["ab"] + totals["bb"] + totals["hbp"] + totals["sf"]
    if denom <= 0:
        woba = None
    else:
        numerator = (
            _WOBA_W_BB * totals["bb"]
            + _WOBA_W_HBP * totals["hbp"]
            + _WOBA_W_1B * singles
            + _WOBA_W_2B * totals["double"]
            + _WOBA_W_3B * totals["triple"]
            + _WOBA_W_HR * totals["hr"]
        )
        woba = numerator / denom

    k_pct = (totals["k"] / totals["pa"] * 100.0) if totals["pa"] else None
    return {
        "woba": woba,
        "k_pct": k_pct,
        "pa": totals["pa"],
    }


def pitcher_metrics_from_logs(rows: Iterable[Mapping[str, Any]]) -> dict[str, Optional[float]]:
    totals = {
        "ip": 0.0,
        "h": 0,
        "er": 0,
        "bb": 0,
        "k": 0,
        "hr": 0,
        "hbp": 0,
    }
    for row in rows:
        totals["ip"] += safe_float(row.get("innings_pitched"), 0.0) or 0.0
        totals["h"] += safe_int(row.get("hits_allowed"))
        totals["er"] += safe_int(row.get("earned_runs"))
        totals["bb"] += safe_int(row.get("walks"))
        totals["k"] += safe_int(row.get("strikeouts"))
        totals["hr"] += safe_int(row.get("home_runs_allowed"))
        totals["hbp"] += safe_int(row.get("hit_by_pitch"))

    ip = totals["ip"]
    if ip <= 0:
        return {
            "innings_pitched": 0.0,
            "fip": None,
            "whip": None,
            "hr_per_9": None,
            "k_bb_pct": None,
        }

    fip = ((13 * totals["hr"]) + (3 * (totals["bb"] + totals["hbp"])) - (2 * totals["k"])) / ip + 3.15
    whip = (totals["h"] + totals["bb"]) / ip
    hr_per_9 = totals["hr"] / ip * 9.0
    pa_est = (3 * ip) + totals["h"] + totals["bb"] + totals["hbp"]
    k_bb_pct = ((totals["k"] - totals["bb"]) / pa_est * 100.0) if pa_est > 0 else None
    return {
        "innings_pitched": ip,
        "fip": fip,
        "whip": whip,
        "hr_per_9": hr_per_9,
        "k_bb_pct": k_bb_pct,
    }


def batter_total_bases(stats: Mapping[str, Any]) -> int:
    hits = safe_int(stats.get("hits"))
    doubles = safe_int(stats.get("doubles"))
    triples = safe_int(stats.get("triples"))
    homers = safe_int(stats.get("homeRuns"))
    return hits + doubles + (2 * triples) + (3 * homers)


def batting_outcome(stats: Mapping[str, Any]) -> dict[str, Any]:
    hits = safe_int(stats.get("hits"))
    return {
        "at_bats": safe_int(stats.get("atBats")),
        "hits": hits,
        "doubles": safe_int(stats.get("doubles")),
        "triples": safe_int(stats.get("triples")),
        "home_runs": safe_int(stats.get("homeRuns")),
        "total_bases": batter_total_bases(stats),
        "walks": safe_int(stats.get("baseOnBalls")),
        "strikeouts": safe_int(stats.get("strikeOuts")),
        "rbi": safe_int(stats.get("rbi")),
        "runs": safe_int(stats.get("runs")),
        "hit": hits >= 1,
        "tb_2_plus": batter_total_bases(stats) >= 2,
        "hr": safe_int(stats.get("homeRuns")) >= 1,
        "bust": batter_total_bases(stats) == 0,
    }


def cold_form_multiplier(form_ratio: Optional[float]) -> float:
    if form_ratio is None:
        return 1.0
    if form_ratio < 0.75:
        return 0.30
    if form_ratio < 0.85:
        return 0.50
    return 1.0


def weighted_score(
    config: WeightConfig,
    *,
    signal_values: Mapping[str, float],
    park_multiplier: float,
    cold_multiplier: float,
) -> float:
    raw = (
        config.platoon * signal_values["platoon"]
        + config.pitcher * signal_values["pitcher_vulnerability"]
        + config.form * signal_values["recent_form"]
        + config.bvp * signal_values["bvp"]
    )
    return round(raw * park_multiplier * cold_multiplier * 100.0, 1)


def bucket_for_score(score: float) -> str:
    if score >= 70:
        return "70+"
    if score >= 60:
        return "60-69"
    if score >= 50:
        return "50-59"
    return "<50"


def summarize_picks(picks: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(picks)
    if not count:
        return {
            "picks": 0,
            "avg_score": None,
            "hit_rate": None,
            "tb_2_plus_rate": None,
            "hr_rate": None,
            "avg_total_bases": None,
            "bust_rate": None,
        }

    def rate(key: str) -> float:
        return sum(1 for pick in picks if pick["outcome"][key]) / count

    return {
        "picks": count,
        "avg_score": round(statistics.mean(pick["score"] for pick in picks), 2),
        "hit_rate": round(rate("hit"), 4),
        "tb_2_plus_rate": round(rate("tb_2_plus"), 4),
        "hr_rate": round(rate("hr"), 4),
        "avg_total_bases": round(statistics.mean(pick["outcome"]["total_bases"] for pick in picks), 3),
        "bust_rate": round(rate("bust"), 4),
    }


def render_pct(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.1f}%"


def render_num(value: Optional[float]) -> str:
    if value is None:
        return "-"
    if isinstance(value, int):
        return str(value)
    return f"{value:.2f}"


def print_table(headers: list[str], rows: list[list[Any]]) -> None:
    widths = [len(header) for header in headers]
    rendered_rows = [[str(cell) for cell in row] for row in rows]
    for row in rendered_rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))

    header_line = "  ".join(header.ljust(widths[idx]) for idx, header in enumerate(headers))
    sep = "  ".join("-" * width for width in widths)
    print(header_line)
    print(sep)
    for row in rendered_rows:
        print("  ".join(cell.ljust(widths[idx]) for idx, cell in enumerate(row)))


class OutcomeBacktester:
    def __init__(
        self,
        *,
        db: Database,
        cache_dir: Path,
        refresh_cache: bool = False,
        request_delay_seconds: float = 0.08,
        min_pitcher_ip: float = 20.0,
        rolling_days: int = 14,
        min_score: float = 50.0,
        min_fired_signals: int = 2,
        top_n_per_day: int = 10,
    ) -> None:
        self.db = db
        self.cache_dir = cache_dir
        self.refresh_cache = refresh_cache
        self.request_delay_seconds = request_delay_seconds
        self.min_pitcher_ip = min_pitcher_ip
        self.rolling_days = rolling_days
        self.min_score = min_score
        self.min_fired_signals = min_fired_signals
        self.top_n_per_day = top_n_per_day

        self.batter_logs_by_player: dict[int, list[dict[str, Any]]] = {}
        self.pitcher_logs_by_player: dict[int, list[dict[str, Any]]] = {}
        self.bats_by_player: dict[int, Optional[str]] = {}
        self.throws_by_pitcher: dict[int, Optional[str]] = {}

    async def load_db_context(self) -> dict[str, Any]:
        batter_rows = await self.db.fetch_all("select * from batter_game_logs order by game_date")
        pitcher_rows = await self.db.fetch_all("select * from pitcher_game_logs order by game_date")
        player_rows = await self.db.fetch_all("select mlb_id, bats from players where mlb_id is not null")
        pitcher_identity_rows = await self.db.fetch_all("select mlb_id, throws from pitchers where mlb_id is not null")

        self.batter_logs_by_player = self._group_by_player(batter_rows)
        self.pitcher_logs_by_player = self._group_by_player(pitcher_rows)
        self.bats_by_player = {
            int(row["mlb_id"]): row["bats"]
            for row in player_rows
            if row["mlb_id"] is not None
        }
        self.throws_by_pitcher = {
            int(row["mlb_id"]): row["throws"]
            for row in pitcher_identity_rows
            if row["mlb_id"] is not None
        }

        min_date = min((row["game_date"] for row in batter_rows), default=None)
        max_date = max((row["game_date"] for row in batter_rows), default=None)
        return {
            "batter_game_logs": len(batter_rows),
            "pitcher_game_logs": len(pitcher_rows),
            "players_with_bats": len(self.bats_by_player),
            "pitchers_with_throws": len(self.throws_by_pitcher),
            "game_log_min_date": min_date,
            "game_log_max_date": max_date,
        }

    @staticmethod
    def _group_by_player(rows: Iterable[Any]) -> dict[int, list[dict[str, Any]]]:
        grouped: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            data = dict(row._mapping if hasattr(row, "_mapping") else row)
            pid = data.get("player_id")
            if pid is None:
                continue
            grouped.setdefault(int(pid), []).append(data)
        return grouped

    async def fetch_slate_games(self, target: date) -> list[dict[str, Any]]:
        schedule = self._fetch_schedule(target)
        games = []
        for game in schedule:
            if str(game.get("status") or "").lower() not in {"final", "game over"}:
                continue
            game_id = safe_int(game.get("game_id"))
            if not game_id:
                continue
            game_data = self._fetch_game(game_id)
            if game_data:
                games.append({"schedule": game, "game": game_data})
        return games

    def _fetch_schedule(self, target: date) -> list[dict[str, Any]]:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = self.cache_dir / f"schedule_{target.isoformat()}.json"
        if cache_path.exists() and not self.refresh_cache:
            return json.loads(cache_path.read_text(encoding="utf-8"))

        schedule = statsapi.schedule(
            start_date=target.strftime("%m/%d/%Y"),
            end_date=target.strftime("%m/%d/%Y"),
        )
        cache_path.write_text(json.dumps(schedule, indent=2, sort_keys=True), encoding="utf-8")
        if self.request_delay_seconds:
            time.sleep(self.request_delay_seconds)
        return schedule

    def _fetch_game(self, game_id: int) -> Optional[dict[str, Any]]:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = self.cache_dir / f"game_{game_id}.json"
        if cache_path.exists() and not self.refresh_cache:
            return json.loads(cache_path.read_text(encoding="utf-8"))

        try:
            game_data = statsapi.get("game", {"gamePk": game_id})
        except Exception as exc:
            print(f"Warning: failed to fetch game {game_id}: {exc}")
            return None

        cache_path.write_text(json.dumps(game_data, indent=2, sort_keys=True), encoding="utf-8")
        if self.request_delay_seconds:
            time.sleep(self.request_delay_seconds)
        return game_data

    def pregame_batter_form(self, player_id: int, target: date) -> dict[str, Optional[float]]:
        logs = self.batter_logs_by_player.get(player_id, [])
        target_iso = target.isoformat()
        cutoff_iso = (target - timedelta(days=self.rolling_days)).isoformat()
        season_rows = [row for row in logs if row["game_date"] < target_iso]
        rolling_rows = [
            row
            for row in season_rows
            if row["game_date"] >= cutoff_iso
        ]
        season = woba_from_logs(season_rows)
        rolling = woba_from_logs(rolling_rows)
        return {
            "season_woba": season["woba"],
            "season_pa": season["pa"],
            "rolling_woba": rolling["woba"],
            "rolling_k_pct": rolling["k_pct"],
            "rolling_pa": rolling["pa"],
        }

    def pregame_pitcher_metrics(self, pitcher_id: int, target: date) -> dict[str, Optional[float]]:
        logs = self.pitcher_logs_by_player.get(pitcher_id, [])
        target_iso = target.isoformat()
        previous_rows = [row for row in logs if row["game_date"] < target_iso]
        return pitcher_metrics_from_logs(previous_rows)

    def game_candidates(self, slate_game: Mapping[str, Any], target: date) -> list[dict[str, Any]]:
        schedule = slate_game["schedule"]
        game = slate_game["game"]
        game_data = game.get("gameData", {})
        box_teams = game.get("liveData", {}).get("boxscore", {}).get("teams", {})
        game_players = game_data.get("players", {})
        candidates = []

        for offense_side in ["away", "home"]:
            defense_side = "home" if offense_side == "away" else "away"
            offense = box_teams.get(offense_side, {})
            defense = box_teams.get(defense_side, {})
            batting_order = offense.get("battingOrder", []) or []
            if not batting_order:
                continue

            pitcher_ids = defense.get("pitchers", []) or []
            opposing_pitcher_id = safe_int(pitcher_ids[0]) if pitcher_ids else 0
            if not opposing_pitcher_id:
                probable = game_data.get("probablePitchers", {}).get(defense_side, {})
                opposing_pitcher_id = safe_int(probable.get("id"))
            if not opposing_pitcher_id:
                continue

            pitcher_metrics = self.pregame_pitcher_metrics(opposing_pitcher_id, target)
            if (pitcher_metrics.get("innings_pitched") or 0.0) < self.min_pitcher_ip:
                continue

            pitcher_person = game_players.get(f"ID{opposing_pitcher_id}", {})
            opposing_throws = (
                self.throws_by_pitcher.get(opposing_pitcher_id)
                or (pitcher_person.get("pitchHand") or {}).get("code")
            )
            opposing_pitcher_name = (
                pitcher_person.get("fullName")
                or pitcher_person.get("boxscoreName")
                or pitcher_person.get("fullFMLName")
                or str(opposing_pitcher_id)
            )
            team_name = offense.get("team", {}).get("name") or schedule.get(f"{offense_side}_name")
            venue = schedule.get("venue_name") or game_data.get("venue", {}).get("name")
            park = get_park_factor(venue)
            park_runs_factor = park["runs"] if park else 100

            for slot, player_id_raw in enumerate(batting_order, start=1):
                player_id = safe_int(player_id_raw)
                player = offense.get("players", {}).get(f"ID{player_id}", {})
                person = player.get("person", {})
                game_player = game_players.get(f"ID{player_id}", {})
                batting_stats = player.get("stats", {}).get("batting", {})
                outcome = batting_outcome(batting_stats)
                form = self.pregame_batter_form(player_id, target)
                if not form.get("season_woba") or (form.get("season_pa") or 0) < 20:
                    continue

                bats = (
                    self.bats_by_player.get(player_id)
                    or (game_player.get("batSide") or {}).get("code")
                    or (person.get("batSide") or {}).get("code")
                )

                candidate = self.score_candidate(
                    bats=bats,
                    throws=opposing_throws,
                    pitcher_metrics=pitcher_metrics,
                    form=form,
                    park_runs_factor=park_runs_factor,
                )
                candidates.append({
                    **candidate,
                    "date": target.isoformat(),
                    "game_id": safe_int(schedule.get("game_id")),
                    "game_time": schedule.get("game_datetime"),
                    "team": team_name,
                    "player_id": player_id,
                    "player_name": (
                        person.get("fullName")
                        or game_player.get("fullName")
                        or player.get("fullName")
                        or str(player_id)
                    ),
                    "bats": bats,
                    "batting_order": slot,
                    "opposing_pitcher_id": opposing_pitcher_id,
                    "opposing_pitcher_name": opposing_pitcher_name,
                    "opposing_throws": opposing_throws,
                    "venue": venue,
                    "outcome": outcome,
                    "context": {
                        "season_pa": form.get("season_pa"),
                        "rolling_pa": form.get("rolling_pa"),
                        "season_woba": form.get("season_woba"),
                        "rolling_woba": form.get("rolling_woba"),
                        "rolling_k_pct": form.get("rolling_k_pct"),
                        "pitcher_ip": pitcher_metrics.get("innings_pitched"),
                        "pitcher_fip": pitcher_metrics.get("fip"),
                        "pitcher_whip": pitcher_metrics.get("whip"),
                        "park_runs_factor": park_runs_factor,
                    },
                })
        return candidates

    @staticmethod
    def score_candidate(
        *,
        bats: Optional[str],
        throws: Optional[str],
        pitcher_metrics: Mapping[str, Optional[float]],
        form: Mapping[str, Optional[float]],
        park_runs_factor: Optional[int],
    ) -> dict[str, Any]:
        platoon_v, platoon_fired, platoon_detail = score_platoon(bats, throws)
        pitcher_v, pitcher_fired, pitcher_detail = score_pitcher_vulnerability(
            pitcher_metrics.get("fip"),
            pitcher_metrics.get("whip"),
            pitcher_metrics.get("hr_per_9"),
            k_bb_pct=pitcher_metrics.get("k_bb_pct"),
        )
        form_v, form_fired, form_detail, form_ratio = score_recent_form(
            rolling_woba=form.get("rolling_woba"),
            season_woba=form.get("season_woba"),
            rolling_k_pct=form.get("rolling_k_pct"),
        )
        # Historical career BvP can be fetched per pair, but doing that for
        # thousands of backtest rows is slow and noisy. Match the projected
        # lineup behavior by treating it as neutral/no-fire for this runner.
        bvp_v, bvp_fired, bvp_detail = score_bvp(None, None)
        park_multiplier = (park_runs_factor or 100) / 100.0
        park_fired = bool(park_runs_factor and (park_runs_factor > 103 or park_runs_factor < 97))
        cold_multiplier = cold_form_multiplier(form_ratio)

        signals = {
            "platoon": {"value": platoon_v, "fired": platoon_fired, "detail": platoon_detail},
            "pitcher_vulnerability": {"value": pitcher_v, "fired": pitcher_fired, "detail": pitcher_detail},
            "recent_form": {"value": form_v, "fired": form_fired, "detail": form_detail},
            "bvp": {"value": bvp_v, "fired": bvp_fired, "detail": bvp_detail},
            "park_factor": {
                "value": park_multiplier,
                "fired": park_fired,
                "detail": f"runs factor {park_runs_factor or 100}",
            },
        }
        return {
            "signal_values": {
                "platoon": platoon_v,
                "pitcher_vulnerability": pitcher_v,
                "recent_form": form_v,
                "bvp": bvp_v,
            },
            "signals": signals,
            "fired_count": sum(1 for signal in signals.values() if signal["fired"]),
            "park_multiplier": park_multiplier,
            "cold_multiplier": cold_multiplier,
            "form_ratio": form_ratio,
        }

    async def build_candidates(
        self,
        start: date,
        end: date,
        *,
        verbose: bool = True,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        all_candidates: list[dict[str, Any]] = []
        day_stats = []
        for target in date_range(start, end):
            slate_games = await self.fetch_slate_games(target)
            daily_candidates = []
            for slate_game in slate_games:
                daily_candidates.extend(self.game_candidates(slate_game, target))
            all_candidates.extend(daily_candidates)
            day_stats.append({
                "date": target.isoformat(),
                "games": len(slate_games),
                "eligible_batters": len(daily_candidates),
            })
            if verbose:
                print(
                    f"{target.isoformat()}: games={len(slate_games)} "
                    f"eligible_batters={len(daily_candidates)}"
                )
        meta = {
            "days": day_stats,
            "total_games": sum(day["games"] for day in day_stats),
            "total_eligible_batters": len(all_candidates),
        }
        return all_candidates, meta


def evaluate_configs(
    candidates: list[dict[str, Any]],
    configs: list[WeightConfig],
    *,
    min_score: float,
    min_fired_signals: int,
    top_n_per_day: int,
) -> dict[str, Any]:
    candidates_by_day: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        candidates_by_day.setdefault(candidate["date"], []).append(candidate)

    results = {}
    for config in configs:
        selected: list[dict[str, Any]] = []
        daily = []
        for day, day_candidates in sorted(candidates_by_day.items()):
            scored = []
            for candidate in day_candidates:
                score = weighted_score(
                    config,
                    signal_values=candidate["signal_values"],
                    park_multiplier=candidate["park_multiplier"],
                    cold_multiplier=candidate["cold_multiplier"],
                )
                if score < min_score:
                    continue
                if candidate["fired_count"] < min_fired_signals:
                    continue
                scored.append({**candidate, "score": score, "config": config.name})
            scored.sort(key=lambda row: (row["score"], -row["batting_order"]), reverse=True)
            day_picks = scored[:top_n_per_day]
            selected.extend(day_picks)
            daily.append({
                "date": day,
                **summarize_picks(day_picks),
            })

        bucket_summary = {}
        for bucket in ["50-59", "60-69", "70+"]:
            bucket_picks = [pick for pick in selected if bucket_for_score(pick["score"]) == bucket]
            bucket_summary[bucket] = summarize_picks(bucket_picks)

        signal_summary = {}
        for signal_name in ["platoon", "pitcher_vulnerability", "recent_form", "park_factor"]:
            signal_picks = [
                pick
                for pick in selected
                if pick["signals"].get(signal_name, {}).get("fired")
            ]
            signal_summary[signal_name] = summarize_picks(signal_picks)

        results[config.name] = {
            "weights": {
                "platoon": config.platoon,
                "pitcher": config.pitcher,
                "form": config.form,
                "bvp": config.bvp,
            },
            "note": config.note,
            "summary": summarize_picks(selected),
            "days_with_picks": sum(1 for day in daily if day["picks"]),
            "daily": daily,
            "score_buckets": bucket_summary,
            "signals": signal_summary,
            "top_examples": selected[:10],
        }

    return results


def print_results(results: Mapping[str, Any]) -> None:
    ranked = sorted(
        results.items(),
        key=lambda item: (
            item[1]["summary"]["tb_2_plus_rate"] or 0.0,
            -(item[1]["summary"]["bust_rate"] or 1.0),
            item[1]["summary"]["avg_total_bases"] or 0.0,
        ),
        reverse=True,
    )

    print("\nCONFIG SUMMARY")
    print_table(
        ["Rank", "Config", "Picks", "AvgScore", "Hit%", "2+TB%", "HR%", "AvgTB", "Bust%", "Days"],
        [
            [
                idx,
                name,
                data["summary"]["picks"],
                render_num(data["summary"]["avg_score"]),
                render_pct(data["summary"]["hit_rate"]),
                render_pct(data["summary"]["tb_2_plus_rate"]),
                render_pct(data["summary"]["hr_rate"]),
                render_num(data["summary"]["avg_total_bases"]),
                render_pct(data["summary"]["bust_rate"]),
                data["days_with_picks"],
            ]
            for idx, (name, data) in enumerate(ranked, start=1)
        ],
    )

    best_name, best_data = ranked[0]
    print(f"\nBEST BY 2+ TOTAL BASES RATE: {best_name}")
    print(f"Weights: {best_data['weights']}")
    print(best_data["note"])

    print("\nSCORE BUCKETS FOR BEST CONFIG")
    print_table(
        ["Bucket", "Picks", "Hit%", "2+TB%", "HR%", "AvgTB", "Bust%"],
        [
            [
                bucket,
                summary["picks"],
                render_pct(summary["hit_rate"]),
                render_pct(summary["tb_2_plus_rate"]),
                render_pct(summary["hr_rate"]),
                render_num(summary["avg_total_bases"]),
                render_pct(summary["bust_rate"]),
            ]
            for bucket, summary in best_data["score_buckets"].items()
        ],
    )

    print("\nSIGNALS FOR BEST CONFIG")
    print_table(
        ["Signal Fired", "Picks", "Hit%", "2+TB%", "HR%", "AvgTB", "Bust%"],
        [
            [
                signal,
                summary["picks"],
                render_pct(summary["hit_rate"]),
                render_pct(summary["tb_2_plus_rate"]),
                render_pct(summary["hr_rate"]),
                render_num(summary["avg_total_bases"]),
                render_pct(summary["bust_rate"]),
            ]
            for signal, summary in best_data["signals"].items()
        ],
    )

    print("\nTOP EXAMPLES FOR BEST CONFIG")
    print_table(
        ["Date", "Player", "Score", "TB", "H", "HR", "Pitcher", "Signals"],
        [
            [
                pick["date"],
                pick["player_name"],
                pick["score"],
                pick["outcome"]["total_bases"],
                pick["outcome"]["hits"],
                pick["outcome"]["home_runs"],
                pick["opposing_pitcher_name"],
                pick["fired_count"],
            ]
            for pick in best_data["top_examples"]
        ],
    )


async def latest_game_log_date(db: Database) -> date:
    row = await db.fetch_one("select max(game_date) as max_date from batter_game_logs")
    if row is None or row["max_date"] is None:
        raise RuntimeError("No batter_game_logs rows found; cannot determine end date.")
    return parse_iso_date(row["max_date"])


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
        end = parse_iso_date(args.end_date) if args.end_date else await latest_game_log_date(db)
        start = parse_iso_date(args.start_date) if args.start_date else end - timedelta(days=args.days - 1)

        backtester = OutcomeBacktester(
            db=db,
            cache_dir=Path(args.cache_dir),
            refresh_cache=args.refresh_cache,
            request_delay_seconds=args.request_delay_seconds,
            min_pitcher_ip=args.min_pitcher_ip,
            rolling_days=args.rolling_days,
            min_score=args.min_score,
            min_fired_signals=args.min_fired_signals,
            top_n_per_day=args.top_n_per_day,
        )
        db_meta = await backtester.load_db_context()

        print("OUTCOME BACKTEST")
        print(f"Dates: {start.isoformat()} through {end.isoformat()}")
        print(f"DB game logs: {db_meta['game_log_min_date']} through {db_meta['game_log_max_date']}")
        print(
            f"Thresholds: min_score={args.min_score}, "
            f"min_fired_signals={args.min_fired_signals}, "
            f"top_n_per_day={args.top_n_per_day}, "
            f"min_pitcher_ip={args.min_pitcher_ip}"
        )
        print("BvP note: career BvP is neutral/no-fire in this outcome runner.")
        print()

        candidates, slate_meta = await backtester.build_candidates(start, end)
        results = evaluate_configs(
            candidates,
            DEFAULT_WEIGHT_CONFIGS,
            min_score=args.min_score,
            min_fired_signals=args.min_fired_signals,
            top_n_per_day=args.top_n_per_day,
        )

        print_results(results)

        output = {
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "input": {
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "days": args.days,
                "min_score": args.min_score,
                "min_fired_signals": args.min_fired_signals,
                "top_n_per_day": args.top_n_per_day,
                "min_pitcher_ip": args.min_pitcher_ip,
                "rolling_days": args.rolling_days,
            },
            "db_meta": db_meta,
            "slate_meta": slate_meta,
            "results": results,
            "limitations": [
                "Outcome backtest only; historical sportsbook odds are not stored.",
                "Career BvP is treated as neutral/no-fire to avoid thousands of noisy API calls.",
                "Historical lineups and starting pitchers come from completed-game MLB StatsAPI boxscores.",
            ],
        }

        if args.output_json:
            output_path = Path(args.output_json)
        else:
            DEFAULT_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            run_label = (
                f"top{args.top_n_per_day}_"
                f"score{args.min_score:g}_"
                f"signals{args.min_fired_signals}"
            )
            output_path = DEFAULT_RESULTS_DIR / (
                f"outcome_backtest_{start.isoformat()}_{end.isoformat()}_"
                f"{run_label}_{stamp}.json"
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\nSaved JSON: {output_path}")
        return 0
    finally:
        await db.disconnect()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backtest hitter betting-weight configs against actual MLB outcomes.",
    )
    parser.add_argument("--start-date", help="Inclusive YYYY-MM-DD start date.")
    parser.add_argument("--end-date", help="Inclusive YYYY-MM-DD end date. Defaults to latest batter game log date.")
    parser.add_argument("--days", type=int, default=30, help="Number of days ending at --end-date when --start-date is omitted.")
    parser.add_argument("--top-n-per-day", type=int, default=10, help="Max picks retained per config per day.")
    parser.add_argument("--min-score", type=float, default=50.0, help="Minimum composite score to qualify.")
    parser.add_argument("--min-fired-signals", type=int, default=2, help="Minimum fired signals to qualify.")
    parser.add_argument("--min-pitcher-ip", type=float, default=20.0, help="Minimum pregame pitcher IP sample.")
    parser.add_argument("--rolling-days", type=int, default=14, help="Recent-form rolling window length.")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR), help="Directory for MLB StatsAPI schedule/game cache.")
    parser.add_argument("--refresh-cache", action="store_true", help="Refetch MLB StatsAPI data even when cached.")
    parser.add_argument("--request-delay-seconds", type=float, default=0.08, help="Delay between uncached StatsAPI calls.")
    parser.add_argument("--output-json", help="Optional path for the detailed JSON result.")
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
