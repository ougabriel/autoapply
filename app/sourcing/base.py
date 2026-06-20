"""Shared sourcing types + triage (the WAT gates applied during sourcing).

A Sourcer pulls raw vacancies from one pool and triages each through the gates,
returning ranked SourcedCandidates. The coordinator merges them. This keeps the
"find" phase honest: a candidate only survives sourcing if it already passes the
sponsor / recruiter / dedup / no-sponsorship / qualification gates.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from ..models import Profile
from ..services import cv_router, filters, sponsor_match


@dataclass
class SourcedCandidate:
    company: str
    title: str
    url: str
    ats: str | None = None
    source: str = ""
    description: str = ""
    sponsor_matched: bool = False
    lane: str = ""
    fit_score: float = 0.0
    reason: str = ""

    def dedup_key(self) -> tuple[str, str]:
        """Canonical (company, role-family) key for cross-source dedup."""
        company = re.sub(r"[^a-z0-9]", "", self.company.lower())
        # Role family: collapse seniority words so "Senior X" == "X".
        title = self.title.lower()
        title = re.sub(r"\b(senior|junior|lead|principal|staff|sr|jr)\b", "", title)
        title = re.sub(r"[^a-z0-9]", "", title)
        return (company, title)


# Recruitment-agency hard-ban substrings (WAT filter_recruiters). Direct
# employers only; an agency that is the CQC-registered employer is allowed via
# the profile's doNotApply nuance, handled in filters.
RECRUITER_BANS = [
    "recruitment", "recruiters", "staffing", "talent solutions", "resourcing",
    "michael page", "hays", "reed", "adecco", "randstad", "manpower",
    "robert walters", "hudson", "spring", "search consultancy",
]


def looks_like_recruiter(company: str) -> bool:
    low = company.lower()
    return any(ban in low for ban in RECRUITER_BANS)


def score_fit(profile: Profile, title: str, description: str, sponsor_matched: bool) -> float:
    """0-10 fit score.

    Title relevance is the dominant signal: a vacancy whose TITLE does not map to
    one of the candidate's CV lanes is off-target regardless of sponsor status or
    a few stray keyword hits in the body. This stops a sponsor-matched but
    irrelevant role (e.g. "Product Designer" for a DevOps candidate) scoring high.
    """
    title_low = title.lower()
    body_low = f"{title}\n{description}".lower()

    # Title must hit at least one explicit (non catch-all) routing rule.
    title_relevant = False
    for rule in profile.cvRouting:
        pat = rule.match
        if pat.strip() in (".*", "(?i).*"):
            continue
        try:
            if re.search(pat, title):
                title_relevant = True
                break
        except re.error:
            continue

    # Strength overlap across title + body.
    hits = 0
    for strength in profile.skillsTruth.has:
        tokens = [t for t in re.split(r"[\s/]+", strength.lower()) if len(t) > 3]
        if any(t in body_low for t in tokens):
            hits += 1

    if not title_relevant:
        # Off-target title: cap hard so it never out-ranks a real lane match.
        return round(min(2.5, hits * 0.4), 1)

    # Sponsorship only matters when the profile needs it. For non-sponsored /
    # global candidates, judge purely on role fit so nothing is mis-ranked.
    if profile.visa.needsSponsorship:
        overlap = min(6.0, hits * 1.2)
        sponsor_bonus = 2.0 if sponsor_matched else 0.0
        title_bonus = 2.0
    else:
        overlap = min(8.0, hits * 1.6)
        sponsor_bonus = 0.0
        title_bonus = 2.0
    return round(min(10.0, overlap + sponsor_bonus + title_bonus), 1)


def triage(profile: Profile, raw: SourcedCandidate) -> SourcedCandidate | None:
    """Apply the WAT gates to one raw vacancy. Returns an enriched candidate or None."""
    if looks_like_recruiter(raw.company):
        return None

    raw.sponsor_matched = sponsor_match.is_sponsor(raw.company)

    decision = filters.evaluate(profile, raw.company, raw.title, raw.description)
    if not decision.keep:
        return None

    raw.lane = cv_router.route(profile, raw.title, raw.description)
    raw.fit_score = score_fit(profile, raw.title, raw.description, raw.sponsor_matched)
    raw.reason = (
        f"fit {raw.fit_score}/10"
        + (", sponsor-matched" if raw.sponsor_matched else ", sponsor unknown")
        + f", lane {raw.lane}"
    )[:80]
    return raw


@dataclass
class SourcerResult:
    label: str
    candidates: list[SourcedCandidate] = field(default_factory=list)
    exhausted: bool = False
    error: str = ""


class Sourcer(Protocol):
    label: str

    def fetch(self, profile: Profile, cursor) -> SourcerResult:
        """Pull + triage candidates from one pool. Must not open the submit browser."""
        ...
