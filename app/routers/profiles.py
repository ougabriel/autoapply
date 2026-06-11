"""Profile endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..services import profiles as profiles_svc
from ..services import sponsor_match

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


@router.get("")
def list_profiles() -> dict:
    return {
        "profiles": profiles_svc.list_profiles(),
        "sponsor_register_loaded": sponsor_match.register_loaded(),
    }


@router.get("/{name}")
def get_profile(name: str) -> dict:
    try:
        profile = profiles_svc.load_profile(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Profile '{name}' not found")
    return profile.model_dump()
