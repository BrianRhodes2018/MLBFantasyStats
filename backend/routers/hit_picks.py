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
async def get_hit_picks_ledger(
    prediction_window: Optional[str] = Query(
        None,
        pattern="^(morning|afternoon|legacy)$",
    ),
):
    """Running per-model-version track record of graded picks."""
    data = await hit_picks_store.fetch_ledger_summary(
        prediction_window=prediction_window
    )
    if not data["days_graded"]:
        raise HTTPException(
            status_code=404,
            detail="No graded picks yet. Run grade_hit_picks.py first.",
        )
    return ApiResponse(code=200, message="Hit picks ledger", data=data)


@router.get("/boards/{model_role}/latest", response_model=ApiResponse)
async def get_latest_role_board(
    model_role: str,
    top: int = Query(15, ge=1, le=25),
    prediction_window: Optional[str] = Query(
        None,
        pattern="^(morning|afternoon)$",
    ),
):
    """Latest visible V2 primary or V3 challenger evaluation board."""
    if model_role not in {"primary", "challenger"}:
        raise HTTPException(status_code=422, detail="Unknown model role.")
    data = await hit_picks_store.fetch_role_picks(
        model_role=model_role,
        top=top,
        prediction_window=prediction_window,
    )
    if data is None:
        raise HTTPException(
            status_code=404,
            detail=f"No visible {model_role} hit-pick board is available yet.",
        )
    return ApiResponse(code=200, message=f"{model_role.title()} hit picks", data=data)


@router.get("/boards/{model_role}/dates", response_model=ApiResponse)
async def get_role_board_dates(
    model_role: str,
    limit: int = Query(180, ge=1, le=730),
):
    """History calendar for one visible model role."""
    if model_role not in {"primary", "challenger"}:
        raise HTTPException(status_code=422, detail="Unknown model role.")
    data = await hit_picks_store.fetch_role_dates(
        model_role=model_role,
        limit=limit,
    )
    return ApiResponse(code=200, message=f"{model_role.title()} board dates", data=data)


@router.get("/boards/{model_role}/{pick_date}", response_model=ApiResponse)
async def get_role_board_for_date(
    model_role: str,
    pick_date: date,
    top: int = Query(15, ge=1, le=25),
    prediction_window: Optional[str] = Query(
        None,
        pattern="^(morning|afternoon)$",
    ),
):
    """One active role board for a historical date/window."""
    if model_role not in {"primary", "challenger"}:
        raise HTTPException(status_code=422, detail="Unknown model role.")
    data = await hit_picks_store.fetch_role_picks(
        model_role=model_role,
        top=top,
        pick_date=pick_date.isoformat(),
        prediction_window=prediction_window,
    )
    if data is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No visible {model_role} hit-pick board is available for "
                f"{pick_date.isoformat()}."
            ),
        )
    return ApiResponse(code=200, message=f"{model_role.title()} hit picks", data=data)


@router.get("/compare/latest", response_model=ApiResponse)
async def get_latest_hit_pick_comparison(
    top: int = Query(15, ge=1, le=25),
    prediction_window: Optional[str] = Query(
        None,
        pattern="^(morning|afternoon)$",
    ),
):
    """Latest strictly paired V2/V3 comparison."""
    data = await hit_picks_store.fetch_paired_comparison(
        top=top,
        prediction_window=prediction_window,
    )
    if data is None:
        raise HTTPException(status_code=404, detail="No V2 board is available yet.")
    return ApiResponse(code=200, message="V2/V3 hit-pick comparison", data=data)


@router.get("/compare/{pick_date}", response_model=ApiResponse)
async def get_hit_pick_comparison_for_date(
    pick_date: date,
    top: int = Query(15, ge=1, le=25),
    prediction_window: Optional[str] = Query(
        None,
        pattern="^(morning|afternoon)$",
    ),
):
    """Strictly paired V2/V3 comparison for one date/window."""
    data = await hit_picks_store.fetch_paired_comparison(
        pick_date=pick_date.isoformat(),
        prediction_window=prediction_window,
        top=top,
    )
    if data is None:
        raise HTTPException(
            status_code=404,
            detail=f"No V2 board is available for {pick_date.isoformat()}.",
        )
    return ApiResponse(code=200, message="V2/V3 hit-pick comparison", data=data)


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
