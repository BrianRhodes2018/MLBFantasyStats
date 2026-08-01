"""Point-in-time features for daily starting-pitcher strikeout models.

The builder deliberately updates every history only after an entire date has
been featurized. That keeps doubleheaders and same-day outcomes out of the
pregame feature set. Historical lineups are projected from prior batting
orders with the same free recency-weighted helper used by Hit Picks.
"""

from __future__ import annotations

from datetime import date, timedelta
from math import cos, log1p, pi, sin
from typing import Any, Iterable, Mapping, Optional

from build_hit_dataset import (
    BoxscoreSource,
    batting_line_from_boxscore,
    date_range,
    pitching_line_from_boxscore,
    safe_float,
    safe_int,
)
from projected_lineups import weighted_lineup_projection


DEFAULT_LEAGUE_K_RATE = 0.225
DEFAULT_STARTER_BF = 22.5
DEFAULT_STARTER_PITCHES = 88.0
DEFAULT_STARTER_IP = 5.2

PITCHER_K_PRIOR_BF = 90.0
BATTER_K_PRIOR_PA = 55.0
BATTER_HAND_PRIOR_PA = 35.0
TEAM_K_PRIOR_PA = 120.0
VERSUS_TEAM_PRIOR_BF = 70.0

FEATURE_NAMES = [
    "pitcher_k_rate",
    "pitcher_recent_k_rate",
    "pitcher_k_rate_trend",
    "pitcher_starts_log",
    "pitcher_bf_avg",
    "pitcher_recent_bf_avg",
    "pitcher_pitches_avg",
    "pitcher_recent_pitches_avg",
    "pitcher_ip_avg",
    "pitcher_days_rest",
    "pitcher_vs_opponent_k_rate",
    "opponent_lineup_k_rate",
    "opponent_team_k_rate",
    "lineup_history_coverage",
    "lineup_confidence",
    "is_home_pitcher",
    "throws_left",
    "league_k_rate",
    "month_sin",
    "month_cos",
]


def _rate(successes: float, opportunities: float, fallback: float) -> float:
    return successes / opportunities if opportunities > 0 else fallback


def _shrunk_rate(
    successes: float,
    opportunities: float,
    prior_rate: float,
    prior_opportunities: float,
) -> float:
    return (successes + prior_rate * prior_opportunities) / (
        opportunities + prior_opportunities
    )


def _mean(values: Iterable[float], fallback: float) -> float:
    items = list(values)
    return sum(items) / len(items) if items else fallback


def _person_hand(
    game_players: Mapping[str, Any],
    player_id: int,
    field: str,
) -> Optional[str]:
    person = game_players.get(f"ID{player_id}") or {}
    value = ((person.get(field) or {}).get("code") or "").upper()
    return value or None


class PitcherKsDatasetBuilder:
    """Build one row per starting pitcher from prior-only histories."""

    def __init__(self, *, source: BoxscoreSource) -> None:
        self.source = source
        self.pitcher_history: dict[int, list[dict[str, Any]]] = {}
        self.batter_history: dict[int, list[dict[str, Any]]] = {}
        self.batter_hand_history: dict[tuple[int, str], list[dict[str, Any]]] = {}
        self.team_batting_history: dict[str, list[dict[str, Any]]] = {}
        self.pitcher_team_history: dict[tuple[int, str], list[dict[str, Any]]] = {}
        self.lineup_history: dict[str, list[dict[str, Any]]] = {}
        self.pitcher_hands: dict[int, str] = {}
        self.player_names: dict[int, str] = {}
        self.league_strikeouts = 0
        self.league_batters_faced = 0

    @property
    def league_k_rate(self) -> float:
        return _rate(
            self.league_strikeouts,
            self.league_batters_faced,
            DEFAULT_LEAGUE_K_RATE,
        )

    def _pitcher_summary(self, pitcher_id: int, target: date) -> dict[str, float]:
        history = self.pitcher_history.get(pitcher_id, [])
        recent = history[-3:]
        league = self.league_k_rate

        strikeouts = sum(safe_int(row.get("strikeouts")) for row in history)
        batters_faced = sum(safe_int(row.get("batters_faced")) for row in history)
        recent_strikeouts = sum(safe_int(row.get("strikeouts")) for row in recent)
        recent_bf = sum(safe_int(row.get("batters_faced")) for row in recent)

        season_rate = _shrunk_rate(
            strikeouts,
            batters_faced,
            league,
            PITCHER_K_PRIOR_BF,
        )
        recent_rate = _shrunk_rate(
            recent_strikeouts,
            recent_bf,
            season_rate,
            PITCHER_K_PRIOR_BF / 2.0,
        )
        days_rest = 5.0
        if history:
            last_date = date.fromisoformat(str(history[-1]["game_date"]))
            days_rest = float(max(0, min((target - last_date).days, 14)))

        return {
            "season_rate": season_rate,
            "recent_rate": recent_rate,
            "trend": recent_rate - season_rate,
            "starts_log": log1p(len(history)),
            "bf_avg": _mean(
                (safe_float(row.get("batters_faced"), 0.0) or 0.0 for row in history),
                DEFAULT_STARTER_BF,
            ),
            "recent_bf_avg": _mean(
                (safe_float(row.get("batters_faced"), 0.0) or 0.0 for row in recent),
                DEFAULT_STARTER_BF,
            ),
            "pitches_avg": _mean(
                (safe_float(row.get("pitches_thrown"), 0.0) or 0.0 for row in history),
                DEFAULT_STARTER_PITCHES,
            ),
            "recent_pitches_avg": _mean(
                (safe_float(row.get("pitches_thrown"), 0.0) or 0.0 for row in recent),
                DEFAULT_STARTER_PITCHES,
            ),
            "ip_avg": _mean(
                (safe_float(row.get("innings_pitched"), 0.0) or 0.0 for row in history),
                DEFAULT_STARTER_IP,
            ),
            "days_rest": days_rest,
        }

    def _batter_k_rate(self, batter_id: int, pitcher_hand: Optional[str]) -> tuple[float, bool]:
        league = self.league_k_rate
        overall = self.batter_history.get(batter_id, [])
        overall_pa = sum(safe_int(row.get("plate_appearances")) for row in overall)
        overall_k = sum(safe_int(row.get("strikeouts")) for row in overall)
        overall_rate = _shrunk_rate(
            overall_k,
            overall_pa,
            league,
            BATTER_K_PRIOR_PA,
        )
        hand = (pitcher_hand or "").upper()
        if not hand:
            return overall_rate, overall_pa > 0
        split = self.batter_hand_history.get((batter_id, hand), [])
        split_pa = sum(safe_int(row.get("plate_appearances")) for row in split)
        split_k = sum(safe_int(row.get("strikeouts")) for row in split)
        split_rate = _shrunk_rate(
            split_k,
            split_pa,
            overall_rate,
            BATTER_HAND_PRIOR_PA,
        )
        return (0.65 * overall_rate) + (0.35 * split_rate), overall_pa > 0

    def _team_k_rate(self, team: str) -> float:
        rows = self.team_batting_history.get(team, [])[-12:]
        pa = sum(safe_int(row.get("plate_appearances")) for row in rows)
        strikeouts = sum(safe_int(row.get("strikeouts")) for row in rows)
        return _shrunk_rate(
            strikeouts,
            pa,
            self.league_k_rate,
            TEAM_K_PRIOR_PA,
        )

    def _lineup_summary(
        self,
        *,
        team: str,
        order: Iterable[int],
        pitcher_hand: Optional[str],
        confidence: Mapping[int, float],
    ) -> dict[str, float]:
        rates: list[float] = []
        confidences: list[float] = []
        covered = 0
        for player_id in list(order)[:9]:
            rate, has_history = self._batter_k_rate(player_id, pitcher_hand)
            rates.append(rate)
            confidences.append(float(confidence.get(player_id, 1.0)))
            covered += int(has_history)
        team_rate = self._team_k_rate(team)
        lineup_rate = _mean(rates, team_rate)
        return {
            "lineup_rate": (0.8 * lineup_rate) + (0.2 * team_rate),
            "team_rate": team_rate,
            "coverage": covered / 9.0,
            "confidence": _mean(confidences, 0.0),
        }

    def _versus_team_rate(self, pitcher_id: int, opponent: str) -> float:
        rows = self.pitcher_team_history.get((pitcher_id, opponent), [])
        strikeouts = sum(safe_int(row.get("strikeouts")) for row in rows)
        batters_faced = sum(safe_int(row.get("batters_faced")) for row in rows)
        pitcher_rate = self._pitcher_summary(pitcher_id, date.today())["season_rate"]
        return _shrunk_rate(
            strikeouts,
            batters_faced,
            pitcher_rate,
            VERSUS_TEAM_PRIOR_BF,
        )

    def make_features(
        self,
        *,
        pitcher_id: int,
        pitcher_hand: Optional[str],
        opponent: str,
        projected_order: Iterable[int],
        lineup_confidence: Mapping[int, float],
        target: date,
        is_home_pitcher: bool,
    ) -> dict[str, float]:
        pitcher = self._pitcher_summary(pitcher_id, target)
        lineup = self._lineup_summary(
            team=opponent,
            order=projected_order,
            pitcher_hand=pitcher_hand,
            confidence=lineup_confidence,
        )
        angle = 2.0 * pi * (target.month - 1) / 12.0
        return {
            "pitcher_k_rate": pitcher["season_rate"],
            "pitcher_recent_k_rate": pitcher["recent_rate"],
            "pitcher_k_rate_trend": pitcher["trend"],
            "pitcher_starts_log": pitcher["starts_log"],
            "pitcher_bf_avg": pitcher["bf_avg"],
            "pitcher_recent_bf_avg": pitcher["recent_bf_avg"],
            "pitcher_pitches_avg": pitcher["pitches_avg"],
            "pitcher_recent_pitches_avg": pitcher["recent_pitches_avg"],
            "pitcher_ip_avg": pitcher["ip_avg"],
            "pitcher_days_rest": pitcher["days_rest"],
            "pitcher_vs_opponent_k_rate": self._versus_team_rate(pitcher_id, opponent),
            "opponent_lineup_k_rate": lineup["lineup_rate"],
            "opponent_team_k_rate": lineup["team_rate"],
            "lineup_history_coverage": lineup["coverage"],
            "lineup_confidence": lineup["confidence"],
            "is_home_pitcher": float(is_home_pitcher),
            "throws_left": float((pitcher_hand or "").upper() == "L"),
            "league_k_rate": self.league_k_rate,
            "month_sin": sin(angle),
            "month_cos": cos(angle),
        }

    def _project_lineup(
        self,
        team: str,
        pitcher_hand: Optional[str],
        target: date,
    ) -> tuple[list[int], dict[int, float], str]:
        projection = weighted_lineup_projection(
            self.lineup_history.get(team, []),
            pitcher_hand,
            target.isoformat(),
        )
        if not projection:
            return [], {}, "team-rate fallback"
        order = [safe_int(player_id) for player_id in projection.get("order") or []]
        order = [player_id for player_id in order if player_id]
        return order, dict(projection.get("share") or {}), "projected"

    def rows_for_date(self, target: date) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        updates: list[dict[str, Any]] = []

        for slate_game in self.source.final_games(target):
            schedule = slate_game["schedule"]
            if str(schedule.get("game_type") or "R") != "R":
                continue
            game = slate_game["game"]
            game_data = game.get("gameData", {})
            game_players = game_data.get("players", {})
            box_teams = game.get("liveData", {}).get("boxscore", {}).get("teams", {})
            game_update: dict[str, Any] = {
                "target": target,
                "game_players": game_players,
                "box_teams": box_teams,
            }

            for defense_side in ("away", "home"):
                offense_side = "home" if defense_side == "away" else "away"
                defense = box_teams.get(defense_side, {})
                offense = box_teams.get(offense_side, {})
                pitcher_ids = defense.get("pitchers") or []
                pitcher_id = safe_int(pitcher_ids[0]) if pitcher_ids else 0
                if not pitcher_id:
                    continue
                pitcher_box = (defense.get("players") or {}).get(f"ID{pitcher_id}", {})
                pitching = (pitcher_box.get("stats") or {}).get("pitching") or {}
                line = pitching_line_from_boxscore(pitching)
                if not line.get("started") or safe_int(line.get("batters_faced")) <= 0:
                    continue
                pitcher_hand = (
                    self.pitcher_hands.get(pitcher_id)
                    or _person_hand(game_players, pitcher_id, "pitchHand")
                )
                opponent = (offense.get("team") or {}).get("name") or str(
                    schedule.get(f"{offense_side}_name") or ""
                )
                team = (defense.get("team") or {}).get("name") or str(
                    schedule.get(f"{defense_side}_name") or ""
                )
                order, confidence, lineup_source = self._project_lineup(
                    opponent,
                    pitcher_hand,
                    target,
                )
                feature_values = self.make_features(
                    pitcher_id=pitcher_id,
                    pitcher_hand=pitcher_hand,
                    opponent=opponent,
                    projected_order=order,
                    lineup_confidence=confidence,
                    target=target,
                    is_home_pitcher=defense_side == "home",
                )
                rows.append({
                    "game_date": target.isoformat(),
                    "game_pk": safe_int(schedule.get("game_id")),
                    "game_time": schedule.get("game_datetime"),
                    "pitcher_id": pitcher_id,
                    "pitcher_name": (
                        (pitcher_box.get("person") or {}).get("fullName")
                        or (game_players.get(f"ID{pitcher_id}") or {}).get("fullName")
                        or str(pitcher_id)
                    ),
                    "team": team,
                    "opponent": opponent,
                    "venue": schedule.get("venue_name"),
                    "pitcher_throws": pitcher_hand,
                    "lineup_source": lineup_source,
                    "strikeouts": safe_int(line.get("strikeouts")),
                    "batters_faced": safe_int(line.get("batters_faced")),
                    "pitches_thrown": safe_int(line.get("pitches_thrown")),
                    "innings_pitched": safe_float(line.get("innings_pitched"), 0.0) or 0.0,
                    **feature_values,
                })
            updates.append(game_update)

        for update in updates:
            self._apply_game(update)
        return rows

    def _apply_game(self, update: Mapping[str, Any]) -> None:
        target: date = update["target"]
        target_iso = target.isoformat()
        game_players = update["game_players"]
        box_teams = update["box_teams"]
        starter_hand_by_side: dict[str, Optional[str]] = {}

        for side in ("away", "home"):
            team_box = box_teams.get(side, {})
            pitcher_ids = team_box.get("pitchers") or []
            starter_id = safe_int(pitcher_ids[0]) if pitcher_ids else 0
            starter_hand = (
                self.pitcher_hands.get(starter_id)
                or _person_hand(game_players, starter_id, "pitchHand")
            )
            starter_hand_by_side[side] = starter_hand
            if starter_id and starter_hand:
                self.pitcher_hands[starter_id] = starter_hand

        for side in ("away", "home"):
            opponent_side = "home" if side == "away" else "away"
            team_box = box_teams.get(side, {})
            team = (team_box.get("team") or {}).get("name") or ""
            opponent = (box_teams.get(opponent_side, {}).get("team") or {}).get("name") or ""
            opposing_hand = starter_hand_by_side.get(opponent_side)
            order = [safe_int(player_id) for player_id in (team_box.get("battingOrder") or [])[:9]]
            order = [player_id for player_id in order if player_id]
            if team and order:
                self.lineup_history.setdefault(team, []).append({
                    "date": target_iso,
                    "opp_hand": opposing_hand,
                    "order": order,
                })

            team_pa = 0
            team_k = 0
            for box_player in (team_box.get("players") or {}).values():
                player_id = safe_int((box_player.get("person") or {}).get("id"))
                if not player_id:
                    continue
                person = game_players.get(f"ID{player_id}") or {}
                name = (box_player.get("person") or {}).get("fullName") or person.get("fullName")
                if name:
                    self.player_names[player_id] = name
                batting = (box_player.get("stats") or {}).get("batting") or {}
                pa = safe_int(batting.get("plateAppearances"))
                if pa <= 0:
                    continue
                line = batting_line_from_boxscore(batting)
                line["game_date"] = target_iso
                line["plate_appearances"] = pa
                self.batter_history.setdefault(player_id, []).append(line)
                if opposing_hand:
                    self.batter_hand_history.setdefault(
                        (player_id, opposing_hand), []
                    ).append(line)
                team_pa += pa
                team_k += safe_int(line.get("strikeouts"))
            if team and team_pa:
                self.team_batting_history.setdefault(team, []).append({
                    "game_date": target_iso,
                    "plate_appearances": team_pa,
                    "strikeouts": team_k,
                })

            pitcher_ids = team_box.get("pitchers") or []
            starter_id = safe_int(pitcher_ids[0]) if pitcher_ids else 0
            if not starter_id:
                continue
            starter_box = (team_box.get("players") or {}).get(f"ID{starter_id}", {})
            pitching = (starter_box.get("stats") or {}).get("pitching") or {}
            line = pitching_line_from_boxscore(pitching)
            if not line.get("started") or safe_int(line.get("batters_faced")) <= 0:
                continue
            line["game_date"] = target_iso
            self.pitcher_history.setdefault(starter_id, []).append(line)
            if opponent:
                self.pitcher_team_history.setdefault((starter_id, opponent), []).append(line)
            self.league_strikeouts += safe_int(line.get("strikeouts"))
            self.league_batters_faced += safe_int(line.get("batters_faced"))

    def build(self, start: date, end: date, *, verbose: bool = False) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if end < start:
            return rows
        for target in date_range(start, end):
            day_rows = self.rows_for_date(target)
            rows.extend(day_rows)
            if verbose:
                print(f"{target.isoformat()}: pitcher-starts={len(day_rows)}")
        return rows

    def daily_candidates(
        self,
        *,
        slate: Iterable[Mapping[str, Any]],
        target: date,
        confirmed_lineups: Optional[Mapping[int, Mapping[str, list[int]]]] = None,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for game in slate:
            status = str((game.get("status") or {}).get("detailedState") or "").lower()
            if status in {"postponed", "cancelled", "suspended"}:
                continue
            game_pk = safe_int(game.get("gamePk"))
            teams = game.get("teams") or {}
            for defense_side in ("away", "home"):
                offense_side = "home" if defense_side == "away" else "away"
                defense = teams.get(defense_side) or {}
                offense = teams.get(offense_side) or {}
                probable = defense.get("probablePitcher") or {}
                pitcher_id = safe_int(probable.get("id"))
                if not pitcher_id:
                    continue
                team = ((defense.get("team") or {}).get("name") or "").strip()
                opponent = ((offense.get("team") or {}).get("name") or "").strip()
                if not team or not opponent:
                    continue
                pitcher_hand = (
                    self.pitcher_hands.get(pitcher_id)
                    or ((probable.get("pitchHand") or {}).get("code") or "").upper()
                    or None
                )
                confirmed = (confirmed_lineups or {}).get(game_pk, {}).get(offense_side)
                if confirmed:
                    order = list(confirmed)
                    confidence = {player_id: 1.0 for player_id in order}
                    lineup_source = "confirmed"
                else:
                    order, confidence, lineup_source = self._project_lineup(
                        opponent,
                        pitcher_hand,
                        target,
                    )
                features = self.make_features(
                    pitcher_id=pitcher_id,
                    pitcher_hand=pitcher_hand,
                    opponent=opponent,
                    projected_order=order,
                    lineup_confidence=confidence,
                    target=target,
                    is_home_pitcher=defense_side == "home",
                )
                candidates.append({
                    "game_date": target.isoformat(),
                    "game_pk": game_pk,
                    "game_time": game.get("gameDate"),
                    "pitcher_id": pitcher_id,
                    "pitcher_name": probable.get("fullName") or self.player_names.get(pitcher_id) or str(pitcher_id),
                    "team": team,
                    "opponent": opponent,
                    "venue": (game.get("venue") or {}).get("name"),
                    "pitcher_throws": pitcher_hand,
                    "lineup_source": lineup_source,
                    **features,
                })
        return candidates


def build_training_rows(
    *,
    source: BoxscoreSource,
    first_season: int,
    last_date: date,
    verbose: bool = False,
) -> list[dict[str, Any]]:
    """Build seasons independently so offseason history never leaks forward."""
    all_rows: list[dict[str, Any]] = []
    for season in range(first_season, last_date.year + 1):
        season_start = date(season, 3, 1)
        season_end = min(last_date, date(season, 11, 15))
        if season_end < season_start:
            continue
        builder = PitcherKsDatasetBuilder(source=source)
        season_rows = builder.build(season_start, season_end, verbose=verbose)
        all_rows.extend(season_rows)
        if verbose:
            print(f"season {season}: {len(season_rows)} starts")
    return all_rows


def replay_current_season(
    *,
    source: BoxscoreSource,
    target: date,
    verbose: bool = False,
) -> PitcherKsDatasetBuilder:
    builder = PitcherKsDatasetBuilder(source=source)
    builder.build(date(target.year, 3, 1), target - timedelta(days=1), verbose=verbose)
    return builder
