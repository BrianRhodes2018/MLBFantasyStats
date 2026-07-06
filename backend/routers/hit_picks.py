"""Hit picks routes — serve the daily model pick list and its track record.

The heavy lifting (training the model, projecting lineups, scoring the
slate) happens offline in predict_hits_today.py, which stores each day's
ranked picks in the shared `hit_picks` database table; grade_hit_picks.py
fills in the graded outcome columns the next morning. These endpoints
just read that table — no model runs inside a request — which is what
lets the DEPLOYED backend serve picks generated on the dev machine.
"""

from fastapi import APIRouter, HTTPException

import hit_picks_store
from database import database
from schemas import ApiResponse

router = APIRouter(prefix="/hit-picks", tags=["hit-picks"])


@router.get("/latest", response_model=ApiResponse)
async def get_latest_hit_picks(top: int = 15):
    """The most recent daily pick list, trimmed to the top N."""
    data = await hit_picks_store.fetch_latest_picks(database, top=top)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail="No picks stored yet. Run predict_hits_today.py first.",
        )
    return ApiResponse(code=200, message="Latest hit picks", data=data)


@router.get("/ledger", response_model=ApiResponse)
async def get_hit_picks_ledger():
    """Running per-model-version track record of graded picks."""
    data = await hit_picks_store.fetch_ledger_summary(database)
    if not data["days_graded"]:
        raise HTTPException(
            status_code=404,
            detail="No graded picks yet. Run grade_hit_picks.py first.",
        )
    return ApiResponse(code=200, message="Hit picks ledger", data=data)
