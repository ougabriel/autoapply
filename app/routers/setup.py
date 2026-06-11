"""Setup + activation endpoints.

This is where you ACTIVATE THE MODEL and get the loop ready to go live:
  - configure the LLM provider + API key (model activation)
  - open the persistent browser once to sign into LinkedIn / NHS / ATS
  - check the routed CV PDFs exist on disk
  - a single readiness summary the UI uses to gate Live mode
"""
from __future__ import annotations

import threading

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config
from ..services import cv_router, llm, profiles as profiles_svc, secrets, sponsor_match

router = APIRouter(prefix="/api/setup", tags=["setup"])


@router.get("/status")
def setup_status() -> dict:
    return {
        "llm": {**secrets.status(), "active": llm.is_active()},
        "sponsor_register_loaded": sponsor_match.register_loaded(),
        "browser_profile_dir": str(config.BROWSER_PROFILE_DIR),
        "browser_profile_exists": config.BROWSER_PROFILE_DIR.exists(),
        "cv_dir": str(config.CV_DIR),
    }


# --------------------------------------------------------------------------- #
# Model activation
# --------------------------------------------------------------------------- #
class LLMConfigIn(BaseModel):
    provider: str = "anthropic"  # anthropic | openai
    api_key: str | None = None
    model: str | None = None


@router.post("/llm")
def configure_llm(body: LLMConfigIn) -> dict:
    if body.provider not in ("anthropic", "openai"):
        raise HTTPException(status_code=400, detail="provider must be 'anthropic' or 'openai'")
    secrets.set_value("llm_provider", body.provider)
    if body.model:
        secrets.set_value("llm_model", body.model)
    if body.api_key:
        secrets.set_value(
            "anthropic_api_key" if body.provider == "anthropic" else "openai_api_key",
            body.api_key,
        )
    return {"configured": True, "llm": {**secrets.status(), "active": llm.is_active()}}


@router.post("/llm/test")
def test_llm() -> dict:
    """Generate a tiny sample letter to confirm the model is reachable + gate-clean."""
    if not llm.is_active():
        return {"ok": False, "reason": "no API key configured"}
    names = profiles_svc.list_profiles()
    if not names:
        return {"ok": False, "reason": "no profile to test with"}
    profile = profiles_svc.load_profile(names[0])
    result = llm.generate_letter(
        profile, "Example Care Home", profile.sector,
        "Provide person-centred care and support.", profile.skillsTruth.has[:3],
    )
    if result is None:
        return {"ok": False, "reason": "LLM not active"}
    if result.text.startswith("__LLM_ERROR__"):
        return {"ok": False, "reason": result.text.replace("__LLM_ERROR__:", "")[:200]}
    if result.text == "__GATE_FAILED__":
        return {"ok": False, "reason": "model output failed the integrity gate after retries"}
    return {"ok": True, "provider": result.provider, "model": result.model,
            "sample": result.text[:240]}


class SecretIn(BaseModel):
    key: str
    value: str


@router.post("/secret")
def set_secret(body: SecretIn) -> dict:
    allowed = {"ats_reusable_password"}
    if body.key not in allowed:
        raise HTTPException(status_code=400, detail=f"key must be one of {allowed}")
    secrets.set_value(body.key, body.value)
    return {"set": body.key}


# --------------------------------------------------------------------------- #
# Browser login (one-time, headed)
# --------------------------------------------------------------------------- #
_login_threads: dict[str, threading.Thread] = {}


class LoginIn(BaseModel):
    url: str = "https://www.linkedin.com/login"


@router.post("/browser/login")
def browser_login(body: LoginIn) -> dict:
    """Open the persistent browser at a sign-in URL so the user can log in once.

    The window stays open until the user closes it; the session persists in the
    profile dir for all future submissions. Runs on a thread so the API returns.
    """
    if _login_threads.get("login") and _login_threads["login"].is_alive():
        return {"opened": False, "reason": "a login window is already open"}

    def _open():
        try:
            from ..submit import browser
            with browser.browser_session(profile_dir=config.BROWSER_PROFILE_DIR) as page:
                page.goto(body.url)
                # Hold the context open for up to 5 minutes for the user to sign in.
                page.wait_for_timeout(300_000)
        except Exception:  # noqa: BLE001 - browser may not be installed yet
            pass
        finally:
            _login_threads.pop("login", None)

    t = threading.Thread(target=_open, name="browser-login", daemon=True)
    _login_threads["login"] = t
    t.start()
    return {"opened": True, "url": body.url,
            "note": "Sign in in the window that opened; the session is saved for submissions."}


# --------------------------------------------------------------------------- #
# CV file readiness
# --------------------------------------------------------------------------- #
@router.get("/cv/{candidate}")
def cv_status(candidate: str) -> dict:
    try:
        profile = profiles_svc.load_profile(candidate)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Profile '{candidate}' not found")
    lanes = []
    for lane in profile.cvLanes:
        cv_file = cv_router.cv_file_for_lane(profile, lane)
        path = config.CV_DIR / cv_file if cv_file else None
        lanes.append({
            "lane": lane,
            "cv_file": cv_file,
            "exists": bool(path and path.exists()),
        })
    return {"cv_dir": str(config.CV_DIR), "lanes": lanes,
            "all_present": all(l["exists"] for l in lanes)}


@router.get("/readiness/{candidate}")
def readiness(candidate: str) -> dict:
    """One summary the UI uses to decide whether Live mode is safe to run."""
    try:
        profile = profiles_svc.load_profile(candidate)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Profile '{candidate}' not found")

    cv = cv_status(candidate)
    checks = {
        "llm_active": llm.is_active(),
        "sponsor_register_loaded": sponsor_match.register_loaded(),
        "browser_profile_exists": config.BROWSER_PROFILE_DIR.exists(),
        "cvs_present": cv["all_present"],
    }
    # LLM is optional (template fallback exists); the rest matter for Live.
    live_ready = checks["browser_profile_exists"] and checks["cvs_present"]
    return {"candidate": candidate, "checks": checks, "live_ready": live_ready,
            "cv": cv, "notes": _readiness_notes(checks)}


def _readiness_notes(checks: dict) -> list[str]:
    notes = []
    if not checks["llm_active"]:
        notes.append("LLM not activated - letters use the deterministic template (still honest).")
    if not checks["sponsor_register_loaded"]:
        notes.append("Sponsor register not loaded - copy uk_sponsors.csv into data/.")
    if not checks["browser_profile_exists"]:
        notes.append("Browser profile not created - run a one-time sign-in before Live mode.")
    if not checks["cvs_present"]:
        notes.append("Some routed CV PDFs are missing from the cv/ dir.")
    return notes
