"""Hit picks routes — serve the daily model pick list and its track record.

The heavy lifting (training the model, projecting lineups, scoring the
slate) happens offline in predict_hits_today.py, which writes
hit_picks_YYYY-MM-DD.json files. grade_hit_picks.py later scores those
picks against real boxscores into a ledger file. These endpoints just
read those artifacts — no model runs inside a request.

Directories are env-overridable (HIT_PICKS_DIR / HIT_PICKS_LEDGER) so
tests and deployments can point elsewhere.
"""

import json
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException

from schemas import ApiResponse

router = APIRouter(prefix="/hit-picks", tags=["hit-picks"])

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_PICK_FILE_RE = re.compile(r"hit_picks_(\d{4}-\d{2}-\d{2})\.json$")

# Candidate fields exposed to the frontend (the full pick file also
# carries every model feature, which the UI doesn't need).
_PUBLIC_FIELDS = [
    "player_id", "player_name", "team", "opponent", "venue",
    "batting_order", "bats", "pitcher_name", "pitcher_throws",
    "lineup_source", "hit_probability", "season_hit_per_pa",
    "last10_hit_per_pa", "platoon_advantage",
]


def _picks_dir() -> Path:
    return Path(os.environ.get("HIT_PICKS_DIR", _BACKEND_DIR / "backtest_results"))


def _ledger_path() -> Path:
    return Path(os.environ.get("HIT_PICKS_LEDGER", _BACKEND_DIR / "data" / "hit_picks_ledger.json"))


def _pick_files() -> list[Path]:
    return sorted(
        (p for p in _picks_dir().glob("hit_picks_*.json") if _PICK_FILE_RE.search(p.name)),
    )


def _load_picks(path: Path, top: int) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    candidates = payload.get("candidates", [])[: max(top, 0)]
    return {
        "date": payload.get("date"),
        "generated_at": payload.get("generated_at"),
        "model_version": payload.get("model_version") or "hit_logistic_v1",
        "trained_on_rows": payload.get("trained_on_rows"),
        "picks": [
            {field: candidate.get(field) for field in _PUBLIC_FIELDS}
            for candidate in candidates
        ],
    }


@router.get("/latest", response_model=ApiResponse)
async def get_latest_hit_picks(top: int = 15):
    """The most recent daily pick list, trimmed to the top N."""
    files = _pick_files()
    if not files:
        raise HTTPException(
            status_code=404,
            detail="No pick files found. Run predict_hits_today.py first.",
        )
    return ApiResponse(
        code=200,
        message="Latest hit picks",
        data=_load_picks(files[-1], top),
    )


@router.get("/ledger", response_model=ApiResponse)
async def get_hit_picks_ledger():
    """Running per-model-version track record from grade_hit_picks.py."""
    path = _ledger_path()
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="No ledger yet. Run grade_hit_picks.py first.",
        )
    ledger = json.loads(path.read_text(encoding="utf-8"))
    return ApiResponse(
        code=200,
        message="Hit picks ledger",
        data={
            "summary": ledger.get("summary", {}),
            "days_graded": len(ledger.get("entries", {})),
        },
    )
