"""Projected lineup provider helpers for betting candidates.

SportsDataIO is the first provider target because its MLB projections feed
offers projected and confirmed lineups before MLB StatsAPI usually exposes
official batting orders. The app should still treat MLB-confirmed lineups as
the primary truth source; these helpers fill the early-day gaps.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
import re
from typing import Any, Iterable, Optional

import httpx


PROJECTED_LINEUP_EDGE_THRESHOLD = 0.08
CONFIRMED_LINEUP_EDGE_THRESHOLD = 0.05
SPORTSDATAIO_PROVIDER = "sportsdataio"


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
