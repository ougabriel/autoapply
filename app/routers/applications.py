"""Application endpoints: record outcomes honestly and list the tracker."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from .. import db
from ..models import ApplicationStatus

router = APIRouter(prefix="/api/applications", tags=["applications"])


class ApplicationIn(BaseModel):
    candidate: str
    job_id: int
    company: str
    title: str
    lane: str
    status: ApplicationStatus
    ats: str | None = None
    confirmation_url: str | None = None
    note: str = ""


@router.post("")
def record_application(app: ApplicationIn) -> dict:
    app_id = db.add_application(
        candidate=app.candidate,
        job_id=app.job_id,
        company=app.company,
        title=app.title,
        lane=app.lane,
        status=app.status.value,
        ats=app.ats,
        confirmation_url=app.confirmation_url,
        note=app.note,
    )
    return {"application_id": app_id}


@router.get("")
def list_applications(candidate: str) -> dict:
    apps = db.list_applications(candidate)
    counts: dict[str, int] = {}
    for a in apps:
        counts[a["status"]] = counts.get(a["status"], 0) + 1
    return {"applications": apps, "counts": counts}
