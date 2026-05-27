"""Projected lineup provider helpers for betting candidates.

SportsDataIO is the first provider target because its MLB projections feed
offers projected and confirmed lineups before MLB StatsAPI usually exposes
official batting orders. The app should still treat MLB-confirmed lineups as
the primary truth source; these helpers fill the early-day gaps.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
import re
import time
from typing import Any, Iterable, Mapping, Optional, Sequence

import httpx


PROJECTED_LINEUP_EDGE_THRESHOLD = 0.08
CONFIRMED_LINEUP_EDGE_THRESHOLD = 0.05
SPORTSDATAIO_PROVIDER = "sportsdataio"
RECENT_LINEUPS_PROVIDER = "mlb_recent_lineups"
DEFAULT_RECENT_LINEUP_LOOKBACK_DAYS = 14
DEFAULT_RECENT_LINEUP_CONFIDENCE_FLOOR = 0.50
RECENT_LINEUP_MIN_SPLIT_GAMES = 3
RECENT_LINEUP_MAX_LAST_SEEN_DAYS = 7
_RECENT_LINEUP_CACHE_TTL_SECONDS = 20 * 60
_RECENT_LINEUP_CACHE: dict[tuple, tuple[float, "ProjectedLineupsResult"]] = {}


TEAM_ALIASES = {
    "ARI": "Arizona Diamondbacks",
    "ATL": "Atlanta Braves",
    "BAL": "Baltimore Orioles",
    "BOS": "Boston Red Sox",
    "CHC": "Chicago Cubs",
    "CHW": "Chicago White Sox",
    "CIN": "Cincinnati Reds",
    "CLE": "Cleveland Guardians",
    "COL": "Colorado Rockies",
    "DET": "Detroit Tigers",
    "HOU": "Houston Astros",
    "KC": "Kansas City Royals",
    "KCR": "Kansas City Royals",
    "LAA": "Los Angeles Angels",
    "LAD": "Los Angeles Dodgers",
    "MIA": "Miami Marlins",
    "MIL": "Milwaukee Brewers",
    "MIN": "Minnesota Twins",
    "NYM": "New York Mets",
    "NYY": "New York Yankees",
    "OAK": "Athletics",
    "ATH": "Athletics",
    "PHI": "Philadelphia Phillies",
    "PIT": "Pittsburgh Pirates",
    "SD": "San Diego Padres",
    "SDP": "San Diego Padres",
    "SEA": "Seattle Mariners",
    "SF": "San Francisco Giants",
    "SFG": "San Francisco Giants",
    "STL": "St. Louis Cardinals",
    "TB": "Tampa Bay Rays",
    "TBR": "Tampa Bay Rays",
    "TEX": "Texas Rangers",
    "TOR": "Toronto Blue Jays",
    "WSH": "Washington Nationals",
    "WAS": "Washington Nationals",
}


def normalize_name(value: Optional[str]) -> str:
    """Normalize player names for provider-to-MLB ID matching."""
    if not value:
        return ""
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def team_key(value: Optional[str]) -> str:
    """Return a stable key for a team abbreviation or full team name."""
    if not value:
        return ""
    raw = str(value).strip()
    full_name = TEAM_ALIASES.get(raw.upper(), raw)
    return normalize_name(full_name)


def team_display_name(value: Optional[str]) -> str:
    if not value:
        return ""
    return TEAM_ALIASES.get(str(value).strip().upper(), str(value).strip())


def _to_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ProjectedLineupPlayer:
    name: str
    team: str
    batting_order: int
    position: Optional[str]
    confirmed: bool
    provider: str = SPORTSDATAIO_PROVIDER
    provider_player_id: Optional[int] = None
    fetched_at: Optional[str] = None
    confidence: Optional[float] = None
    sample_size: Optional[int] = None
    games_considered: Optional[int] = None
    split: Optional[str] = None
    last_seen: Optional[str] = None

    @property
    def lineup_source(self) -> str:
        return "confirmed" if self.confirmed else "projected"


@dataclass(frozen=True)
class ProjectedLineupsResult:
    players: list[ProjectedLineupPlayer]
    provider: str
    status: str
    fetched_at: Optional[str] = None
    message: Optional[str] = None
    meta: Optional[dict[str, Any]] = None


def parse_sportsdataio_starting_lineups(
    payload: Iterable[dict[str, Any]],
    *,
    fetched_at: Optional[str] = None,
) -> list[ProjectedLineupPlayer]:
    """Parse SportsDataIO StartingLineupsByDate rows into normalized players."""
    players: list[ProjectedLineupPlayer] = []
    for row in payload or []:
        name = row.get("Name")
        if not name:
            first = row.get("FirstName") or ""
            last = row.get("LastName") or ""
            name = f"{first} {last}".strip()
        batting_order = _to_int(row.get("BattingOrder"))
        if not name or not batting_order:
            continue

        starting = row.get("Starting", row.get("Started"))
        if starting is False or starting == 0:
            continue

        position = row.get("Position")
        if position and str(position).upper() in {"P", "SP", "RP"}:
            continue

        injury_status = str(row.get("InjuryStatus") or "").lower()
        if injury_status in {"out", "doubtful"}:
            continue

        team = team_display_name(row.get("Team"))
        confirmed = bool(row.get("Confirmed"))
        players.append(
            ProjectedLineupPlayer(
                name=name,
                team=team,
                batting_order=batting_order,
                position=position,
                confirmed=confirmed,
                provider_player_id=_to_int(row.get("PlayerID") or row.get("PlayerId")),
                fetched_at=fetched_at,
            )
        )

    players.sort(key=lambda p: (team_key(p.team), p.batting_order, p.name))
    return players


def group_lineups_by_team(
    players: Iterable[ProjectedLineupPlayer],
) -> dict[str, list[ProjectedLineupPlayer]]:
    grouped: dict[str, list[ProjectedLineupPlayer]] = {}
    for player in players:
        key = team_key(player.team)
        if not key:
            continue
        grouped.setdefault(key, []).append(player)
    for team_players in grouped.values():
        team_players.sort(key=lambda p: p.batting_order)
    return grouped


def build_lineup_meta(
    *,
    lineup_mode: str,
    projected_result: Optional[ProjectedLineupsResult],
    unresolved_projected_players: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build response metadata for both populated and empty betting boards."""
    return {
        "mode": lineup_mode,
        "provider": projected_result.provider if projected_result else None,
        "status": projected_result.status if projected_result else "disabled",
        "fetched_at": projected_result.fetched_at if projected_result else None,
        "message": projected_result.message if projected_result else None,
        "available_players": len(projected_result.players) if projected_result else 0,
        "unresolved_players": list(unresolved_projected_players[:20]),
        "provider_meta": projected_result.meta if projected_result and projected_result.meta else {},
        "lineup_counts": {
            "confirmed": sum(1 for row in rows if row.get("lineup_source") == "confirmed"),
            "projected": sum(1 for row in rows if row.get("lineup_source") == "projected"),
        },
    }


def _game_date(game_data: Mapping[str, Any]) -> str:
    datetime_data = game_data.get("gameData", {}).get("datetime", {})
    return (
        datetime_data.get("officialDate")
        or datetime_data.get("originalDate")
        or str(datetime_data.get("dateTime") or "")[:10]
    )


def _game_id(game_data: Mapping[str, Any]) -> str:
    game_meta = game_data.get("gameData", {}).get("game", {})
    return str(game_meta.get("pk") or game_meta.get("gamePk") or _game_date(game_data) or id(game_data))


def _team_name_for_side(game_data: Mapping[str, Any], side: str) -> str:
    box_team = (
        game_data.get("liveData", {})
        .get("boxscore", {})
        .get("teams", {})
        .get(side, {})
        .get("team", {})
        .get("name")
    )
    data_team = (
        game_data.get("gameData", {})
        .get("teams", {})
        .get(side, {})
        .get("name")
    )
    return box_team or data_team or ""


def _pitcher_hand_for_side(game_data: Mapping[str, Any], side: str) -> Optional[str]:
    pitcher = game_data.get("gameData", {}).get("probablePitchers", {}).get(side) or {}
    pitcher_id = _to_int(pitcher.get("id"))
    if not pitcher_id:
        return None
    player = game_data.get("gameData", {}).get("players", {}).get(f"ID{pitcher_id}", {})
    hand = (player.get("pitchHand") or {}).get("code")
    return hand if hand in {"L", "R"} else None


def _lineup_samples_from_game(game_data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Extract one sample per starting batting-order slot from an MLB game."""
    samples: list[dict[str, Any]] = []
    box_teams = game_data.get("liveData", {}).get("boxscore", {}).get("teams", {})
    game_date = _game_date(game_data)
    game_id = _game_id(game_data)

    for side in ("away", "home"):
        team_data = box_teams.get(side, {})
        batting_order = team_data.get("battingOrder") or []
        if not batting_order:
            continue

        team_name = _team_name_for_side(game_data, side)
        tkey = team_key(team_name)
        if not tkey:
            continue

        opposing = "home" if side == "away" else "away"
        opposing_hand = _pitcher_hand_for_side(game_data, opposing) or "ALL"
        players_dict = team_data.get("players", {})

        for slot, raw_pid in enumerate(batting_order[:9], start=1):
            player_id = _to_int(raw_pid)
            if not player_id:
                continue
            player = players_dict.get(f"ID{player_id}", {})
            position = (player.get("position") or {}).get("abbreviation")
            if position and str(position).upper() in {"P", "SP", "RP"}:
                continue
            name = (player.get("person") or {}).get("fullName")
            if not name:
                continue
            samples.append({
                "team": team_name,
                "team_key": tkey,
                "game_id": game_id,
                "game_date": game_date,
                "opposing_hand": opposing_hand,
                "player_id": player_id,
                "name": name,
                "position": position,
                "slot": slot,
            })

    return samples


def _choose_recent_lineup_players(
    team_samples: list[dict[str, Any]],
    *,
    team: str,
    opposing_throw: Optional[str],
    target_date: str,
    min_confidence: float,
    fetched_at: Optional[str],
) -> list[ProjectedLineupPlayer]:
    if not team_samples:
        return []

    split = opposing_throw if opposing_throw in {"L", "R"} else None
    all_game_ids = {sample["game_id"] for sample in team_samples}
    split_samples = [
        sample for sample in team_samples
        if split and sample.get("opposing_hand") == split
    ]
    split_game_ids = {sample["game_id"] for sample in split_samples}

    if split and len(split_game_ids) >= RECENT_LINEUP_MIN_SPLIT_GAMES:
        pool = split_samples
        games_considered = len(split_game_ids)
        split_label = f"vs {split}HP"
    else:
        pool = team_samples
        games_considered = len(all_game_ids)
        split_label = "all"

    if not pool or games_considered <= 0:
        return []

    recent_cutoff = (
        datetime.strptime(target_date, "%Y-%m-%d")
        - timedelta(days=RECENT_LINEUP_MAX_LAST_SEEN_DAYS)
    ).strftime("%Y-%m-%d")

    by_player: dict[int, dict[str, Any]] = {}
    for sample in pool:
        pid = sample["player_id"]
        entry = by_player.setdefault(pid, {
            "player_id": pid,
            "name": sample["name"],
            "team": sample["team"],
            "position": sample.get("position"),
            "starts": 0,
            "slot_sum": 0,
            "slot_counts": Counter(),
            "last_seen": "",
        })
        entry["starts"] += 1
        entry["slot_sum"] += sample["slot"]
        entry["slot_counts"][sample["slot"]] += 1
        if sample.get("game_date", "") > entry["last_seen"]:
            entry["last_seen"] = sample.get("game_date", "")

    eligible = {
        pid: entry
        for pid, entry in by_player.items()
        if entry["last_seen"] >= recent_cutoff
        and (entry["starts"] / games_considered) >= min_confidence
    }
    if not eligible:
        return []

    selected: dict[int, dict[str, Any]] = {}
    used_player_ids: set[int] = set()
    for slot in range(1, 10):
        candidates = [
            entry for pid, entry in eligible.items()
            if pid not in used_player_ids and entry["slot_counts"][slot] > 0
        ]
        if not candidates:
            continue
        candidates.sort(
            key=lambda entry: (
                entry["slot_counts"][slot],
                entry["starts"],
                entry["last_seen"],
            ),
            reverse=True,
        )
        selected[slot] = candidates[0]
        used_player_ids.add(candidates[0]["player_id"])

    remaining = [
        entry for pid, entry in eligible.items()
        if pid not in used_player_ids
    ]
    remaining.sort(
        key=lambda entry: (entry["starts"], entry["last_seen"]),
        reverse=True,
    )
    for entry in remaining:
        empty_slots = [slot for slot in range(1, 10) if slot not in selected]
        if not empty_slots:
            break
        avg_slot = entry["slot_sum"] / max(entry["starts"], 1)
        slot = min(empty_slots, key=lambda candidate_slot: abs(candidate_slot - avg_slot))
        selected[slot] = entry

    players: list[ProjectedLineupPlayer] = []
    for slot in sorted(selected):
        entry = selected[slot]
        confidence = round(entry["starts"] / games_considered, 2)
        players.append(
            ProjectedLineupPlayer(
                name=entry["name"],
                team=team,
                batting_order=slot,
                position=entry.get("position"),
                confirmed=False,
                provider=RECENT_LINEUPS_PROVIDER,
                provider_player_id=entry["player_id"],
                fetched_at=fetched_at,
                confidence=confidence,
                sample_size=entry["starts"],
                games_considered=games_considered,
                split=split_label,
                last_seen=entry["last_seen"],
            )
        )

    return players


def build_recent_mlb_lineup_projections(
    games: Iterable[Mapping[str, Any]],
    *,
    target_date: str,
    target_team_keys: Optional[set[str]] = None,
    opposing_throws_by_team_key: Optional[Mapping[str, Optional[str]]] = None,
    lookback_days: int = DEFAULT_RECENT_LINEUP_LOOKBACK_DAYS,
    min_confidence: float = DEFAULT_RECENT_LINEUP_CONFIDENCE_FLOOR,
    fetched_at: Optional[str] = None,
) -> ProjectedLineupsResult:
    """Project batting orders from recent MLB StatsAPI boxscore lineups."""
    samples_by_team: dict[str, list[dict[str, Any]]] = {}
    team_names: dict[str, str] = {}
    games_used = 0

    target_team_keys = target_team_keys or set()
    for game_data in games or []:
        if not game_data or isinstance(game_data, Exception):
            continue
        game_samples = _lineup_samples_from_game(game_data)
        if game_samples:
            games_used += 1
        for sample in game_samples:
            tkey = sample["team_key"]
            if target_team_keys and tkey not in target_team_keys:
                continue
            samples_by_team.setdefault(tkey, []).append(sample)
            team_names.setdefault(tkey, sample["team"])

    projected_players: list[ProjectedLineupPlayer] = []
    for tkey, team_samples in samples_by_team.items():
        projected_players.extend(
            _choose_recent_lineup_players(
                team_samples,
                team=team_names.get(tkey) or tkey,
                opposing_throw=(opposing_throws_by_team_key or {}).get(tkey),
                target_date=target_date,
                min_confidence=min_confidence,
                fetched_at=fetched_at,
            )
        )

    projected_players.sort(key=lambda p: (team_key(p.team), p.batting_order, p.name))
    status = "ok" if projected_players else "no_data"
    return ProjectedLineupsResult(
        players=projected_players,
        provider=RECENT_LINEUPS_PROVIDER,
        status=status,
        fetched_at=fetched_at,
        message=(
            f"Built from MLB StatsAPI batting orders over the previous {lookback_days} days"
            if projected_players
            else f"No recent MLB batting-order samples met the {min_confidence:.0%} confidence floor"
        ),
        meta={
            "lookback_days": lookback_days,
            "games_used": games_used,
            "teams_projected": len(group_lineups_by_team(projected_players)),
            "confidence_floor": min_confidence,
            "min_split_games": RECENT_LINEUP_MIN_SPLIT_GAMES,
            "max_last_seen_days": RECENT_LINEUP_MAX_LAST_SEEN_DAYS,
        },
    )


async def fetch_recent_mlb_lineups(
    date: str,
    *,
    target_team_keys: Optional[set[str]] = None,
    opposing_throws_by_team_key: Optional[Mapping[str, Optional[str]]] = None,
    lookback_days: int = DEFAULT_RECENT_LINEUP_LOOKBACK_DAYS,
    min_confidence: float = DEFAULT_RECENT_LINEUP_CONFIDENCE_FLOOR,
) -> ProjectedLineupsResult:
    """Fetch recent MLB games and project lineups without a paid provider."""
    cache_key = (
        date,
        lookback_days,
        round(min_confidence, 3),
        tuple(sorted(target_team_keys or [])),
        tuple(sorted((opposing_throws_by_team_key or {}).items())),
    )
    cached = _RECENT_LINEUP_CACHE.get(cache_key)
    now = time.time()
    if cached and now - cached[0] < _RECENT_LINEUP_CACHE_TTL_SECONDS:
        return cached[1]

    import statsapi

    target_dt = datetime.strptime(date, "%Y-%m-%d")
    start_dt = target_dt - timedelta(days=lookback_days)
    end_dt = target_dt - timedelta(days=1)
    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    try:
        schedule = await asyncio.to_thread(
            statsapi.schedule,
            start_date=start_dt.strftime("%m/%d/%Y"),
            end_date=end_dt.strftime("%m/%d/%Y"),
        )
    except Exception as exc:
        return ProjectedLineupsResult(
            players=[],
            provider=RECENT_LINEUPS_PROVIDER,
            status="error",
            fetched_at=fetched_at,
            message=str(exc),
        )

    recent_games = [
        game for game in schedule
        if _to_int(game.get("game_id"))
        and str(game.get("status") or "").lower() in {"final", "game over"}
        and (
            not target_team_keys
            or team_key(game.get("home_name")) in target_team_keys
            or team_key(game.get("away_name")) in target_team_keys
        )
    ]

    semaphore = asyncio.Semaphore(12)

    async def fetch_boxscore(client: httpx.AsyncClient, game: Mapping[str, Any]):
        game_id = _to_int(game.get("game_id"))
        if not game_id:
            return None
        async with semaphore:
            response = await client.get(f"https://statsapi.mlb.com/api/v1/game/{game_id}/boxscore")
            response.raise_for_status()
            boxscore = response.json()
            return {
                "gameData": {
                    "game": {"pk": game_id},
                    "datetime": {"officialDate": game.get("game_date")},
                    "teams": {
                        "away": {"name": game.get("away_name")},
                        "home": {"name": game.get("home_name")},
                    },
                    "probablePitchers": {},
                    "players": {},
                },
                "liveData": {"boxscore": boxscore},
            }

    async with httpx.AsyncClient(timeout=20.0) as client:
        game_results = await asyncio.gather(
            *[fetch_boxscore(client, game) for game in recent_games],
            return_exceptions=True,
        )

    result = build_recent_mlb_lineup_projections(
        game_results,
        target_date=date,
        target_team_keys=target_team_keys,
        opposing_throws_by_team_key=opposing_throws_by_team_key,
        lookback_days=lookback_days,
        min_confidence=min_confidence,
        fetched_at=fetched_at,
    )
    _RECENT_LINEUP_CACHE[cache_key] = (now, result)
    return result


async def fetch_sportsdataio_lineups(date: str) -> ProjectedLineupsResult:
    """Fetch SportsDataIO projected/confirmed lineups when configured."""
    api_key = os.environ.get("SPORTSDATAIO_API_KEY")
    if not api_key:
        return ProjectedLineupsResult(
            players=[],
            provider=SPORTSDATAIO_PROVIDER,
            status="not_configured",
            message="SPORTSDATAIO_API_KEY is not configured",
        )

    base_url = os.environ.get(
        "SPORTSDATAIO_MLB_PROJECTIONS_BASE_URL",
        "https://api.sportsdata.io/v3/mlb/projections/json",
    ).rstrip("/")
    url = f"{base_url}/StartingLineupsByDate/{date}"
    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url, params={"key": api_key})
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        return ProjectedLineupsResult(
            players=[],
            provider=SPORTSDATAIO_PROVIDER,
            status="error",
            fetched_at=fetched_at,
            message=str(exc),
        )

    return ProjectedLineupsResult(
        players=parse_sportsdataio_starting_lineups(payload, fetched_at=fetched_at),
        provider=SPORTSDATAIO_PROVIDER,
        status="ok",
        fetched_at=fetched_at,
    )


def candidate_score_floor(
    *,
    lineup_source: str,
    min_composite_score: float,
    projected_lineup_edge_threshold: float = PROJECTED_LINEUP_EDGE_THRESHOLD,
) -> float:
    """Apply the projected-lineup risk premium to the normal quality floor."""
    if lineup_source == "projected":
        return min_composite_score + (projected_lineup_edge_threshold * 100.0)
    return min_composite_score
