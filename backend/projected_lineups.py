"""Projected lineup provider helpers for betting candidates.

SportsDataIO is the first provider target because its MLB projections feed
offers projected and confirmed lineups before MLB StatsAPI usually exposes
official batting orders. The app should still treat MLB-confirmed lineups as
the primary truth source; these helpers fill the early-day gaps.
"""

from __future__ import annotations

import asyncio
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


# Lineups from the most recent week count double in the projection: recent
# role changes (a bench player becoming the everyday leadoff hitter) should
# outvote stale lineups from two weeks ago.
LINEUP_RECENCY_WINDOW_DAYS = 7
LINEUP_RECENCY_WEIGHT = 2.0


def weighted_lineup_projection(
    entries: Sequence[Mapping[str, Any]],
    opposing_hand: Optional[str],
    target_date: str,
) -> Optional[dict[str, Any]]:
    """
    THE lineup-projection formula — shared by the matchups/betting pages
    and the daily hit picks (predict_hits_today.py) so every part of the
    app projects the same starters.

    entries: one dict per recent game, oldest first or any order:
        {"date": "YYYY-MM-DD", "opp_hand": "L"/"R"/None, "order": [player ids]}

    Method: count each player's recency-weighted starts ANYWHERE in the
    order (games within LINEUP_RECENCY_WINDOW_DAYS count
    LINEUP_RECENCY_WEIGHT times) — so a player who bounces between slots
    still registers as an everyday starter. Take the nine highest, order
    them by weighted average slot. Uses only same-handed-starter games
    when there are at least RECENT_LINEUP_MIN_SPLIT_GAMES of them.
    Players not seen within RECENT_LINEUP_MAX_LAST_SEEN_DAYS (injured,
    traded, demoted) are excluded.

    Returns None when there are no entries, else a dict with:
        order       — projected player ids, slot 1 first (up to nine)
        share       — {pid: weighted share of the pool, 1.0 = every game}
        starts      — {pid: raw start count in the pool}
        last_seen   — {pid: most recent start date}
        pool_games  — how many games informed the projection
        split_label — "vs LHP" / "vs RHP" / "all recent games"
    """
    if not entries:
        return None

    hand = (opposing_hand or "").upper()
    same_hand = [e for e in entries if hand and (e.get("opp_hand") or "").upper() == hand]
    pool = same_hand if len(same_hand) >= RECENT_LINEUP_MIN_SPLIT_GAMES else list(entries)
    split_label = f"vs {hand}HP" if pool is same_hand else "all recent games"

    target = datetime.strptime(target_date, "%Y-%m-%d")
    week_ago_iso = (target - timedelta(days=LINEUP_RECENCY_WINDOW_DAYS)).strftime("%Y-%m-%d")
    stale_cutoff_iso = (target - timedelta(days=RECENT_LINEUP_MAX_LAST_SEEN_DAYS)).strftime("%Y-%m-%d")

    weight_by_player: dict[int, float] = {}
    slot_sum_by_player: dict[int, float] = {}
    starts_by_player: dict[int, int] = {}
    last_seen_by_player: dict[int, str] = {}
    total_weight = 0.0
    for entry in pool:
        entry_date = entry.get("date") or ""
        weight = LINEUP_RECENCY_WEIGHT if entry_date >= week_ago_iso else 1.0
        total_weight += weight
        for slot, player_id in enumerate(entry.get("order") or [], start=1):
            weight_by_player[player_id] = weight_by_player.get(player_id, 0.0) + weight
            slot_sum_by_player[player_id] = slot_sum_by_player.get(player_id, 0.0) + weight * slot
            starts_by_player[player_id] = starts_by_player.get(player_id, 0) + 1
            if entry_date > last_seen_by_player.get(player_id, ""):
                last_seen_by_player[player_id] = entry_date

    eligible = [
        pid for pid in weight_by_player
        if last_seen_by_player.get(pid, "") >= stale_cutoff_iso
    ]
    starters = sorted(eligible, key=lambda pid: weight_by_player[pid], reverse=True)[:9]
    starters.sort(key=lambda pid: slot_sum_by_player[pid] / weight_by_player[pid])

    return {
        "order": starters,
        "share": {
            pid: round(weight_by_player[pid] / total_weight, 4) if total_weight else 0.0
            for pid in starters
        },
        "starts": {pid: starts_by_player[pid] for pid in starters},
        "last_seen": {pid: last_seen_by_player[pid] for pid in starters},
        "pool_games": len(pool),
        "split_label": split_label,
    }


def _choose_recent_lineup_players(
    team_samples: list[dict[str, Any]],
    *,
    team: str,
    opposing_throw: Optional[str],
    target_date: str,
    min_confidence: float,
    fetched_at: Optional[str],
) -> list[ProjectedLineupPlayer]:
    """Project a team's lineup from recent per-slot samples.

    Selection is delegated to weighted_lineup_projection — the same
    formula the daily hit picks use — so the matchups page and the hit
    picks page can never disagree about who is projected to start.
    `min_confidence` is applied afterwards as a display floor on the
    weighted share (low-confidence tail-end players are hidden, not
    re-slotted).
    """
    if not team_samples:
        return []

    # Rebuild one entry per game from the flat per-slot samples.
    games: dict[str, dict[str, Any]] = {}
    info_by_player: dict[int, dict[str, Any]] = {}
    for sample in team_samples:
        game = games.setdefault(sample["game_id"], {
            "date": sample.get("game_date", ""),
            "opp_hand": sample.get("opposing_hand"),
            "slots": {},
        })
        game["slots"][sample["slot"]] = sample["player_id"]
        info_by_player.setdefault(sample["player_id"], {
            "name": sample["name"],
            "position": sample.get("position"),
        })

    entries = [
        {
            "date": game["date"],
            "opp_hand": game["opp_hand"],
            "order": [game["slots"][slot] for slot in sorted(game["slots"])],
        }
        for game in games.values()
    ]
    split = opposing_throw if opposing_throw in {"L", "R"} else None
    projection = weighted_lineup_projection(entries, split, target_date)
    if not projection or not projection["order"]:
        return []

    players: list[ProjectedLineupPlayer] = []
    slot = 0
    for player_id in projection["order"]:
        confidence = round(projection["share"][player_id], 2)
        if confidence < min_confidence:
            continue
        slot += 1
        info = info_by_player.get(player_id, {})
        players.append(
            ProjectedLineupPlayer(
                name=info.get("name") or str(player_id),
                team=team,
                batting_order=slot,
                position=info.get("position"),
                confirmed=False,
                provider=RECENT_LINEUPS_PROVIDER,
                provider_player_id=player_id,
                fetched_at=fetched_at,
                confidence=confidence,
                sample_size=projection["starts"][player_id],
                games_considered=projection["pool_games"],
                split=projection["split_label"],
                last_seen=projection["last_seen"][player_id],
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
