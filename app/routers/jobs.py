"""Job endpoints: add a sourced job, list jobs, and evaluate a job end-to-end.

The /evaluate endpoint runs the full pre-submission pipeline for one job:
sponsor match -> Stage 2 filters -> CV lane routing -> tailoring -> integrity gate.
It returns a clear, honest verdict without submitting anything.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db
from ..services import cv_router, filters, profiles as profiles_svc, sponsor_match, tailoring

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class JobIn(BaseModel):
    candidate: str
    company: str
    title: str
    url: str
    ats: str | None = None
    source: str | None = None
    description: str = ""


@router.post("")
def add_job(job: JobIn) -> dict:
    matched = sponsor_match.is_sponsor(job.company)
    job_id = db.upsert_job(
        candidate=job.candidate,
        company=job.company,
        title=job.title,
        url=job.url,
        ats=job.ats,
        source=job.source,
        sponsor_matched=matched,
        description=job.description,
    )
    return {"job_id": job_id, "sponsor_matched": matched}


@router.get("")
def list_jobs(candidate: str) -> dict:
    return {"jobs": db.list_jobs(candidate)}


@router.post("/evaluate")
def evaluate_job(job: JobIn) -> dict:
    """Full pre-submission pipeline for a single job. Submits nothing."""
    try:
        profile = profiles_svc.load_profile(job.candidate)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Profile '{job.candidate}' not found")

    sponsor = sponsor_match.is_sponsor(job.company)
    decision = filters.evaluate(profile, job.company, job.title, job.description)

    result: dict = {
        "company": job.company,
        "title": job.title,
        "sponsor_matched": sponsor,
        "filter_keep": decision.keep,
        "filter_reason": decision.reason,
    }
    if not decision.keep:
        result["verdict"] = "Skip"
        return result

    lane = cv_router.route(profile, job.title, job.description)
    cv_file = cv_router.cv_file_for_lane(profile, lane)
    tailored = tailoring.build_letter(profile, job.company, job.title, job.description, lane)

    result.update(
        {
            "lane": lane,
            "cv_file": cv_file,
            "matched_strengths": tailored.matched_strengths,
            "letter": tailored.letter,
            "integrity_ok": tailored.gate.ok,
            "integrity_violations": tailored.gate.violations,
            "integrity_warnings": tailored.gate.warnings,
            "verdict": "Ready" if tailored.gate.ok else "Blocked-by-integrity-gate",
        }
    )
    return result
