"""Point-in-time lineup and park-factor helpers for the corrected E1 baseline."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from projected_lineups import weighted_lineup_projection


BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_PARK_SNAPSHOT_PATH = (
    BACKEND_DIR / "reference_data" / "park_factor_snapshots.json"
)
LINEUP_LOOKBACK_DAYS = 14


class PointInTimeParkFactors:
    """Select only a park snapshot effective before the historical game."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        snapshots = list(payload.get("snapshots") or [])
        if not snapshots:
            raise ValueError("Park-factor snapshot payload is empty.")
        self.snapshots = sorted(snapshots, key=lambda row: row["effective_date"])
        self.neutral_fallback = dict(
            payload.get("neutral_fallback")
            or {"runs": 100, "hr": 100, "source": "neutral_unavailable"}
        )

    @classmethod
    def from_path(
        cls,
        path: Path | str = DEFAULT_PARK_SNAPSHOT_PATH,
    ) -> "PointInTimeParkFactors":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def lookup(self, venue: Optional[str], game_date: date | str) -> dict[str, Any]:
        target = (
            game_date
            if isinstance(game_date, date)
            else date.fromisoformat(str(game_date))
        )
        eligible = [
            snapshot
            for snapshot in self.snapshots
            if date.fromisoformat(snapshot["effective_date"]) <= target
        ]
        if not eligible:
            return {
                **self.neutral_fallback,
                "venue": venue,
                "effective_date": None,
                "source_season": None,
            }
        snapshot = eligible[-1]
        factor = (snapshot.get("factors") or {}).get(venue or "")
        if factor is None:
            return {
                **self.neutral_fallback,
                "venue": venue,
                "effective_date": snapshot["effective_date"],
                "source_season": snapshot["source_season"],
            }
        return {
            **factor,
            "venue": venue,
            "source": "baseball_savant_prior_season",
            "effective_date": snapshot["effective_date"],
            "source_season": snapshot["source_season"],
        }


def filter_prior_lineups(
    entries: Sequence[Mapping[str, Any]],
    target_date: date | str,
    *,
    lookback_days: int = LINEUP_LOOKBACK_DAYS,
) -> list[Mapping[str, Any]]:
    target = (
        target_date
        if isinstance(target_date, date)
        else date.fromisoformat(str(target_date))
    )
    earliest = target - timedelta(days=lookback_days)
    filtered = []
    for entry in entries:
        entry_date = date.fromisoformat(str(entry["date"]))
        if earliest <= entry_date < target:
            filtered.append(entry)
    return filtered


def project_lineup_point_in_time(
    entries: Sequence[Mapping[str, Any]],
    opposing_hand: Optional[str],
    target_date: date | str,
    *,
    lookback_days: int = LINEUP_LOOKBACK_DAYS,
) -> Optional[dict[str, Any]]:
    target_iso = (
        target_date.isoformat()
        if isinstance(target_date, date)
        else str(target_date)
    )
    prior = filter_prior_lineups(
        entries,
        target_iso,
        lookback_days=lookback_days,
    )
    return weighted_lineup_projection(prior, opposing_hand, target_iso)
