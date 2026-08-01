"""Read-only API for published Pitcher Ks projection runs."""

from fastapi import APIRouter, HTTPException

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


@router.get("/compare/latest", response_model=ApiResponse)
async def latest_pitcher_k_comparison():
    data = await store.fetch_latest_comparison()
    if data is None:
        raise HTTPException(
            status_code=404,
            detail="No complete paired Pitcher Ks run has been published yet.",
        )
    return ApiResponse(code=200, message="Latest paired Pitcher Ks comparison", data=data)
