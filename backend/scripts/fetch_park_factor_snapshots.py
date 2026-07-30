"""Fetch prior-season Statcast park factors for point-in-time backtests.

For games in season N, the experiment uses the completed rolling park-factor
table ending in season N-1. That table was available before opening day and
cannot contain outcomes from the game being predicted.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from park_factors import (
    SAVANT_URL_TEMPLATE,
    SAVANT_USER_AGENT,
    _parse_savant_html,
)


def fetch_snapshot(source_season: int) -> dict:
    url = SAVANT_URL_TEMPLATE.format(year=source_season)
    response = httpx.get(
        url,
        headers={"User-Agent": SAVANT_USER_AGENT},
        timeout=30.0,
        follow_redirects=True,
    )
    response.raise_for_status()
    factors, year_range = _parse_savant_html(response.text)
    return {
        "effective_date": f"{source_season + 1}-01-01",
        "source_season": source_season,
        "source_year_range": year_range,
        "source_url": url,
        "factors": dict(sorted(factors.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-seasons",
        nargs="+",
        type=int,
        default=[2022, 2023, 2024, 2025],
    )
    parser.add_argument(
        "--output",
        default=str(
            BACKEND_DIR / "reference_data" / "park_factor_snapshots.json"
        ),
    )
    args = parser.parse_args()
    payload = {
        "contract_version": "point_in_time_park_factors_v1",
        "method": (
            "Season N uses the completed Baseball Savant rolling park-factor "
            "table ending in season N-1."
        ),
        "neutral_fallback": {
            "runs": 100,
            "hr": 100,
            "source": "neutral_unavailable",
        },
        "snapshots": [fetch_snapshot(year) for year in args.source_seasons],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"Saved {len(payload['snapshots'])} snapshots to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
