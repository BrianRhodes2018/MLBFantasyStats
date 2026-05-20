"""Betting routes."""

from fastapi import APIRouter, HTTPException

from betting_math import calculate_prop_edge
from schemas import ApiResponse, HitterPropEdgeIn, HitterPropEdgeOut

router = APIRouter(prefix="/betting", tags=["betting"])


@router.get("/markets", response_model=ApiResponse)
async def get_supported_betting_markets():
    """Return supported starter markets for the betting workflow."""
    return ApiResponse(
        code=200,
        message="Supported betting markets",
        data={
            "hitter_props": [
                "hits",
                "total_bases",
                "home_runs",
                "rbi",
                "runs",
                "walks",
                "strikeouts",
            ]
        },
    )


@router.post("/hitter-prop-edge", response_model=ApiResponse)
async def calculate_hitter_prop_edge(payload: HitterPropEdgeIn):
    """Calculate model edge against a no-vig two-way prop market."""
    try:
        edge = calculate_prop_edge(
            model_over_probability=payload.model_over_probability,
            over_odds=payload.over_odds,
            under_odds=payload.under_odds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    response = HitterPropEdgeOut(
        player_name=payload.player_name,
        market_type=payload.market_type,
        line=payload.line,
        sportsbook=payload.sportsbook,
        **edge,
    )

    return ApiResponse(
        code=200,
        message=f"Calculated edge for {payload.player_name} {payload.market_type}",
        data=response.dict(),
    )
