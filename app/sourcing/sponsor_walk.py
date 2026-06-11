"""Sponsor-register-walk sourcer - deterministic CSV walk from the cursor.

Marches through uk_sponsors.csv from cursor.last_sponsor_row, advancing a bounded
budget of rows per fire (WAT: cursor_advance_budget 200). It filters to
sector-relevant organisation names and emits each as a sponsor lead.

A sponsor lead is a confirmed-can-sponsor employer whose live vacancies still
need to be discovered on their careers page. Resolving the careers page + open
roles is a per-employer step best done by the agent worker (or a future coded
resolver), so this sourcer emits leads with a clear NeedsResolution marker rather
than fabricating vacancy titles it cannot know. That keeps the honesty contract:
we never invent a role that may not exist.
"""
from __future__ import annotations

import csv
import re

from ..models import Profile
from .. import config
from .base import SourcedCandidate, SourcerResult, looks_like_recruiter
from ..services import sponsor_match

# Sector -> keywords that an org name must contain to be a relevant lead.
SECTOR_KEYWORDS = {
    "Health & Social Care": [
        "care", "health", "nursing", "homecare", "home care", "support",
        "hospital", "medical", "hospice", "mencap", "mind", "nhs",
    ],
    "DevOps / Cloud / Platform Engineering": [
        "tech", "software", "digital", "data", "cloud", "systems", "solutions",
        "consulting", "labs", "ai", "analytics", "bank", "fintech",
    ],
}


def _name_column(fieldnames: list[str] | None) -> str | None:
    if not fieldnames:
        return None
    for col in fieldnames:
        if "organisation name" in col.lower() or "organization name" in col.lower():
            return col
    return fieldnames[0]


class SponsorWalkSourcer:
    label = "sponsor_csv_walk_triage"

    def __init__(self, advance_budget: int = 200):
        self._budget = advance_budget

    def fetch(self, profile: Profile, cursor) -> SourcerResult:
        path = config.SPONSOR_CSV
        if not path.exists():
            return SourcerResult(self.label, [], exhausted=True,
                                 error="sponsor register not present")

        keywords = SECTOR_KEYWORDS.get(profile.sector, [])
        start = max(0, int(getattr(cursor, "last_sponsor_row", 0)))
        end = start + self._budget

        leads: list[SourcedCandidate] = []
        last_row = start
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            name_col = _name_column(reader.fieldnames)
            for i, row in enumerate(reader):
                if i < start:
                    continue
                if i >= end:
                    break
                last_row = i + 1
                name = (row.get(name_col, "") if name_col else "").strip()
                if not name or looks_like_recruiter(name):
                    continue
                low = name.lower()
                if keywords and not any(k in low for k in keywords):
                    continue
                leads.append(
                    SourcedCandidate(
                        company=name,
                        title="(careers page to resolve)",
                        url="",
                        ats="unknown",
                        source="sponsor_csv_walk",
                        description="",
                        sponsor_matched=True,  # by definition: it's on the register
                        lane="",
                        fit_score=3.0,  # sponsor-confirmed baseline; rises once a real role is found
                        reason="sponsor-register lead; resolve careers page for live roles",
                    )
                )

        # Advance the cursor so the next fire continues where this one stopped.
        cursor.last_sponsor_row = last_row
        cursor.last_source = "sponsor_csv_walk"
        return SourcerResult(self.label, leads, exhausted=not leads)
