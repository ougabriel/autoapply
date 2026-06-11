"""Run control + agent-worker API + live event stream.

Two audiences:
  - The USER (dashboard): start / pause / stop / status / live event stream.
  - The AGENT WORKER (the hands): claim a batch, ask should-I-continue, evaluate
    the next candidate through the pipeline, and report each outcome honestly.

The worker never decides policy; it asks the orchestrator. This keeps the WAT
control contract in one place.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..services import (
    cv_router,
    event_log,
    filters,
    orchestrator,
    profiles as profiles_svc,
    run_state,
    sponsor_match,
    tailoring,
)

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _profile_or_404(candidate: str):
    try:
        return profiles_svc.load_profile(candidate)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Profile '{candidate}' not found")


# --------------------------------------------------------------------------- #
# User controls
# --------------------------------------------------------------------------- #
@router.get("/status")
def status(candidate: str) -> dict:
    profile = _profile_or_404(candidate)
    return run_state.status(candidate, profile.cadence.dailyTarget)


class StartIn(BaseModel):
    candidate: str
    source: str = "manual-start"


@router.post("/start")
def start(body: StartIn) -> dict:
    _profile_or_404(body.candidate)
    result = orchestrator.start_batch(body.candidate, body.source)
    return {"started": result.started, "reason": result.reason, "batch_id": result.batch_id}


class CandidateIn(BaseModel):
    candidate: str


@router.post("/pause")
def pause(body: CandidateIn) -> dict:
    _profile_or_404(body.candidate)
    orchestrator.pause(body.candidate)
    return {"paused": True}


@router.post("/resume")
def resume(body: CandidateIn) -> dict:
    _profile_or_404(body.candidate)
    result = orchestrator.resume_from_pending(body.candidate)
    return {"started": result.started, "reason": result.reason, "batch_id": result.batch_id}


@router.get("/events")
def events(candidate: str, limit: int = 200) -> dict:
    return {"events": event_log.tail(candidate, limit)}


@router.get("/stream")
async def stream(candidate: str, request: Request) -> StreamingResponse:
    """Server-sent events: live activity feed for the dashboard."""
    queue = event_log.subscribe(candidate)

    async def gen():
        try:
            # Replay the last few events so a fresh connection has context.
            for ev in event_log.tail(candidate, 20):
                yield f"data: {json.dumps(ev)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(ev)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ke-alive\n\n"  # comment frame keeps the connection open
        finally:
            event_log.unsubscribe(candidate, queue)

    return StreamingResponse(gen(), media_type="text/event-stream")


# --------------------------------------------------------------------------- #
# Agent-worker API
# --------------------------------------------------------------------------- #
@router.post("/worker/claim")
def worker_claim(body: StartIn) -> dict:
    """Worker asks to start/own a batch. Mirrors orchestrator.start_batch."""
    _profile_or_404(body.candidate)
    result = orchestrator.start_batch(body.candidate, body.source)
    return {"started": result.started, "reason": result.reason, "batch_id": result.batch_id}


@router.get("/worker/should-continue")
def worker_should_continue(candidate: str) -> dict:
    _profile_or_404(candidate)
    ok, reason = orchestrator.should_continue(candidate)
    return {"continue": ok, "reason": reason}


class EvaluateIn(BaseModel):
    candidate: str
    company: str
    title: str
    url: str
    ats: str | None = None
    description: str = ""


@router.post("/worker/evaluate")
def worker_evaluate(body: EvaluateIn) -> dict:
    """Run a sourced candidate through the full pipeline. Emits events. Submits nothing.

    The worker calls this, then performs the actual browser submission itself,
    then calls /worker/outcome to report the result.
    """
    profile = _profile_or_404(body.candidate)
    sponsor_matched = sponsor_match.is_sponsor(body.company)

    event_log.emit(body.candidate, event_log.KIND_SOURCE,
                   f"Evaluating {body.title} @ {body.company}",
                   company=body.company, title=body.title, ats=body.ats,
                   sponsor_matched=sponsor_matched)

    decision = filters.evaluate(profile, body.company, body.title, body.description)
    if not decision.keep:
        event_log.emit(body.candidate, event_log.KIND_FILTER,
                       f"Filtered out: {decision.reason}", company=body.company)
        return {"verdict": "Skip", "sponsor_matched": sponsor_matched,
                "filter_reason": decision.reason}

    lane = cv_router.route(profile, body.title, body.description)
    cv_file = cv_router.cv_file_for_lane(profile, lane)
    event_log.emit(body.candidate, event_log.KIND_ROUTE,
                   f"Routed to lane '{lane}' ({cv_file})", company=body.company, lane=lane)

    tailored = tailoring.build_letter(profile, body.company, body.title, body.description, lane)
    if not tailored.gate.ok:
        event_log.emit(body.candidate, event_log.KIND_INTEGRITY,
                       f"Integrity gate BLOCKED letter for {body.company}",
                       company=body.company, violations=tailored.gate.violations)
        return {"verdict": "Blocked-by-integrity-gate", "lane": lane,
                "integrity_violations": tailored.gate.violations}

    return {
        "verdict": "Ready",
        "sponsor_matched": sponsor_matched,
        "lane": lane,
        "cv_file": cv_file,
        "matched_strengths": tailored.matched_strengths,
        "letter": tailored.letter,
        "integrity_warnings": tailored.gate.warnings,
    }


class OutcomeIn(BaseModel):
    candidate: str
    company: str
    title: str
    lane: str
    status: str  # Submitted | Skipped-blocked | NeedsUserAction
    ats: str | None = None
    confirmation_url: str | None = None
    note: str = ""


@router.post("/worker/outcome")
def worker_outcome(body: OutcomeIn) -> dict:
    _profile_or_404(body.candidate)
    return orchestrator.record_outcome(
        body.candidate, status=body.status, company=body.company, title=body.title,
        lane=body.lane, ats=body.ats, confirmation_url=body.confirmation_url, note=body.note,
    )


class EndIn(BaseModel):
    candidate: str
    batch_id: str


@router.post("/worker/end-batch")
def worker_end_batch(body: EndIn) -> dict:
    _profile_or_404(body.candidate)
    return orchestrator.end_batch(body.candidate, body.batch_id)
