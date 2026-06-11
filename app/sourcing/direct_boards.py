"""Direct-boards sourcer - deterministic, no LLM needed.

Greenhouse and Workable expose PUBLIC JSON job-board endpoints per company token:
  Greenhouse: https://boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true
  Workable:   https://apply.workable.com/api/v1/widget/accounts/<token>?details=true

We walk a curated list of known sponsor tokens for the candidate's sector, pull
their live vacancies, and triage each through the WAT gates. This is the WAT's
"direct_boards_triage" agent, realized as concurrent HTTP rather than an LLM
because the structure is predictable.

HTTP is injected (`fetch_json`) so this is unit-testable offline.
"""
from __future__ import annotations

import concurrent.futures
from typing import Callable

from ..models import Profile
from .base import SourcedCandidate, SourcerResult, triage

FetchJson = Callable[[str], dict | list]

# Curated sponsor tokens by sector. These are real, well-known UK sponsor
# employers using Greenhouse/Workable. Extend per sector as the loop learns.
GREENHOUSE_TOKENS = {
    "DevOps / Cloud / Platform Engineering": ["monzo", "wise", "deliveroo", "starlingbank"],
    "Health & Social Care": [],  # most care employers use NHS-TRAC / bespoke, not Greenhouse
}
WORKABLE_TOKENS = {
    "DevOps / Cloud / Platform Engineering": [],
    "Health & Social Care": ["cygnethealthcare"],
}


def _greenhouse_url(token: str) -> str:
    return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"


def _workable_url(token: str) -> str:
    return f"https://apply.workable.com/api/v1/widget/accounts/{token}?details=true"


def _parse_greenhouse(token: str, payload: dict) -> list[SourcedCandidate]:
    out: list[SourcedCandidate] = []
    for job in (payload or {}).get("jobs", []):
        out.append(
            SourcedCandidate(
                company=(job.get("company_name") or token).strip(),
                title=(job.get("title") or "").strip(),
                url=job.get("absolute_url", ""),
                ats="Greenhouse",
                source="direct_boards:greenhouse",
                description=_strip_html(job.get("content", "")),
            )
        )
    return out


def _parse_workable(token: str, payload: dict) -> list[SourcedCandidate]:
    out: list[SourcedCandidate] = []
    name = (payload or {}).get("name", token)
    for job in (payload or {}).get("jobs", []):
        out.append(
            SourcedCandidate(
                company=name,
                title=(job.get("title") or "").strip(),
                url=job.get("url") or job.get("application_url", ""),
                ats="Workable",
                source="direct_boards:workable",
                description=job.get("description", "") or job.get("full_description", ""),
            )
        )
    return out


def _strip_html(text: str) -> str:
    import html
    import re

    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


class DirectBoardsSourcer:
    label = "direct_boards_triage"

    def __init__(self, fetch_json: FetchJson, max_workers: int = 6):
        self._fetch = fetch_json
        self._max_workers = max_workers

    def fetch(self, profile: Profile, cursor) -> SourcerResult:
        gh = GREENHOUSE_TOKENS.get(profile.sector, [])
        wk = WORKABLE_TOKENS.get(profile.sector, [])
        jobs: list[tuple[str, str, str]] = (
            [("greenhouse", t, _greenhouse_url(t)) for t in gh]
            + [("workable", t, _workable_url(t)) for t in wk]
        )
        if not jobs:
            return SourcerResult(self.label, [], exhausted=True)

        raw: list[SourcedCandidate] = []
        errors: list[str] = []

        def pull(item):
            kind, token, url = item
            try:
                payload = self._fetch(url)
                if kind == "greenhouse":
                    return _parse_greenhouse(token, payload)
                return _parse_workable(token, payload)
            except Exception as exc:  # noqa: BLE001 - one bad board must not kill the fan-out
                errors.append(f"{token}: {exc}")
                return []

        with concurrent.futures.ThreadPoolExecutor(max_workers=self._max_workers) as ex:
            for result in ex.map(pull, jobs):
                raw.extend(result)

        vetted = [c for c in (triage(profile, r) for r in raw) if c is not None]
        vetted.sort(key=lambda c: c.fit_score, reverse=True)
        return SourcerResult(
            self.label, vetted, exhausted=not vetted, error="; ".join(errors)
        )
