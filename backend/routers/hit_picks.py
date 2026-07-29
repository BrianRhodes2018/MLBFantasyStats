"""Hit picks routes — serve the daily model pick list and its track record.

The heavy lifting (training the model, projecting lineups, scoring the
slate) happens offline in predict_hits_today.py, which stores each day's
ranked picks in the shared `hit_picks` database table; grade_hit_picks.py
fills in the graded outcome columns the next morning. These endpoints
just read that table — no model runs inside a request — which is what
lets the DEPLOYED backend serve picks generated on the dev machine.
"""

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

import hit_picks_store
from schemas import ApiResponse

router = APIRouter(prefix="/hit-picks", tags=["hit-picks"])


@router.get("/latest", response_model=ApiResponse)
async def get_latest_hit_picks(top: int = Query(15, ge=1, le=25)):
    """The most recent daily pick list, trimmed to the top N."""
    data = await hit_picks_store.fetch_latest_picks(top=top)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail="No picks stored yet. Run predict_hits_today.py first.",
        )
    return ApiResponse(code=200, message="Latest hit picks", data=data)


@router.get("/dates", response_model=ApiResponse)
async def get_hit_pick_dates(limit: int = Query(180, ge=1, le=730)):
    """Dates available to the history calendar, newest first."""
    data = await hit_picks_store.fetch_available_dates(limit=limit)
    return ApiResponse(code=200, message="Hit pick dates", data=data)


@router.get("/ledger", response_model=ApiResponse)
async def get_hit_picks_ledger():
    """Running per-model-version track record of graded picks."""
    data = await hit_picks_store.fetch_ledger_summary()
    if not data["days_graded"]:
        raise HTTPException(
            status_code=404,
            detail="No graded picks yet. Run grade_hit_picks.py first.",
        )
    return ApiResponse(code=200, message="Hit picks ledger", data=data)


@router.get("/{pick_date}", response_model=ApiResponse)
async def get_hit_picks_for_date(
    pick_date: date,
    top: int = Query(15, ge=1, le=25),
    model_version: Optional[str] = Query(None, max_length=40),
    run_id: Optional[UUID] = Query(None),
):
    """A historical list; run_id retrieves one immutable audit snapshot."""
    data = await hit_picks_store.fetch_picks_for_date(
        pick_date=pick_date.isoformat(),
        top=top,
        model_version=model_version,
        run_id=str(run_id) if run_id else None,
    )
    if data is None:
        detail = f"No hit picks stored for {pick_date.isoformat()}"
        if model_version:
            detail += f" and model {model_version}"
        if run_id:
            detail += f" and run {run_id}"
        raise HTTPException(status_code=404, detail=detail)
    return ApiResponse(code=200, message="Historical hit picks", data=data)
