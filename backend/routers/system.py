"""System and metadata routes."""

from datetime import datetime

from fastapi import APIRouter

from database import available_seasons

router = APIRouter(tags=["system"])


@router.get("/health")
async def health_check():
    """Health check endpoint used by keep-alive pings and monitoring."""
    return {"status": "ok"}


@router.get("/seasons")
async def get_available_seasons():
    """Return available season snapshots for the frontend season toggle."""
    current_year = str(datetime.now().year)
    return {
        "current": current_year,
        "available": sorted(available_seasons + [current_year]),
    }
