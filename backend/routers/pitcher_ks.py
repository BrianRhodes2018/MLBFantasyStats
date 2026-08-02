"""Read-only API for published and graded Pitcher Ks projection runs."""

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from pitcher_ks import store
from schemas import ApiResponse


router = APIRouter(prefix="/api/pitcher-ks", tags=["pitcher-ks"])


@router.get("/approaches/{approach}/latest", response_model=ApiResponse)
async def latest_pitcher_k_approach(approach: str):
    if approach not in store.APPROACHES:
        raise HTTPException(status_code=422, detail="Unknown Pitcher Ks approach.")
    data = await store.fetch_latest_approach(approach)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail="No Pitcher Ks projections have been published yet.",
        )
    return ApiResponse(code=200, message=f"Latest {approach} Pitcher Ks projections", data=data)


@router.get("/approaches/{approach}/dates", response_model=ApiResponse)
async def pitcher_k_approach_dates(
    approach: str,
    limit: int = Query(180, ge=1, le=730),
):
    if approach not in store.APPROACHES:
        raise HTTPException(status_code=422, detail="Unknown Pitcher Ks approach.")
    data = await store.fetch_approach_dates(approach, limit=limit)
    return ApiResponse(code=200, message=f"{approach} Pitcher Ks dates", data=data)


@router.get("/approaches/{approach}/{projection_date}", response_model=ApiResponse)
async def pitcher_k_approach_for_date(approach: str, projection_date: date):
    if approach not in store.APPROACHES:
        raise HTTPException(status_code=422, detail="Unknown Pitcher Ks approach.")
    data = await store.fetch_approach(
        approach,
        projection_date=projection_date.isoformat(),
    )
    if data is None:
        raise HTTPException(
            status_code=404,
            detail=f"No {approach} Pitcher Ks projections exist for {projection_date.isoformat()}.",
        )
    return ApiResponse(code=200, message=f"Historical {approach} Pitcher Ks projections", data=data)


@router.get("/compare/latest", response_model=ApiResponse)
async def latest_pitcher_k_comparison():
    data = await store.fetch_latest_comparison()
    if data is None:
        raise HTTPException(
            status_code=404,
            detail="No complete paired Pitcher Ks run has been published yet.",
        )
    return ApiResponse(code=200, message="Latest paired Pitcher Ks comparison", data=data)


@router.get("/compare/{projection_date}", response_model=ApiResponse)
async def pitcher_k_comparison_for_date(projection_date: date):
    data = await store.fetch_comparison(projection_date=projection_date.isoformat())
    if data is None:
        raise HTTPException(
            status_code=404,
            detail=f"No paired Pitcher Ks comparison exists for {projection_date.isoformat()}.",
        )
    return ApiResponse(code=200, message="Historical paired Pitcher Ks comparison", data=data)


@router.get("/ledger", response_model=ApiResponse)
async def pitcher_k_ledger():
    data = await store.fetch_ledger_summary()
    return ApiResponse(code=200, message="Pitcher Ks live evaluation ledger", data=data)
