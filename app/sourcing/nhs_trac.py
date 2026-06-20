"""NHS Jobs / TRAC sourcer - agent-hook (the biggest care-sector sponsor pool).

NHS Jobs (jobs.nhs.uk, TRAC) has no clean public API and requires a logged-in
account, so - like LinkedIn - this is an agent-hook, not a scraper. The agent
triages NHS listings in the logged-in browser and posts them via the API; this
sourcer drains and triages them through the same WAT gates as every other source.

This is the RIGHT CHANNEL for care candidates (Racheal), where Greenhouse boards
are mostly irrelevant.
"""
from __future__ import annotations

from ..models import Profile
from .base import SourcedCandidate, SourcerResult, triage

_inbox: dict[str, list[SourcedCandidate]] = {}


def provide(candidate: str, finds: list[SourcedCandidate]) -> int:
    box = _inbox.setdefault(candidate, [])
    box.extend(finds)
    return len(box)


def pending(candidate: str) -> int:
    return len(_inbox.get(candidate, []))


class NhsTracAgentSourcer:
    label = "nhs_trac_triage"

    def __init__(self, candidate_key: str):
        self._key = candidate_key

    def fetch(self, profile: Profile, cursor) -> SourcerResult:
        box = _inbox.get(self._key, [])
        _inbox[self._key] = []
        if not box:
            return SourcerResult(self.label, [], exhausted=True,
                                 error="no agent-provided NHS/TRAC finds this fire")
        vetted = [c for c in (triage(profile, r) for r in box) if c is not None]
        vetted.sort(key=lambda c: c.fit_score, reverse=True)
        return SourcerResult(self.label, vetted, exhausted=not vetted)
