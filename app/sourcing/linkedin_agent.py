"""LinkedIn Easy-Apply sourcer - agent-hook (not a scraper).

LinkedIn's User Agreement prohibits automated scraping, and the EA pool needs
human-grade judgment on messy cards. So this sourcer does NOT hit LinkedIn from
the server. Instead it accepts candidates that the LLM agent worker has already
triaged in the logged-in browser (the WAT "linkedin_ea_triage" sub-agent) and
runs them through the same gates as every other source, so nothing bypasses the
honesty/dedup/sponsor checks.

Flow:
  - The agent worker triages LinkedIn EA cards in the persistent browser session.
  - It posts the raw finds to the run via the worker API (handed to `provide`).
  - At batch start the coordinator calls this sourcer, which drains whatever the
    agent provided for this candidate and triages it.
"""
from __future__ import annotations

from ..models import Profile
from .base import SourcedCandidate, SourcerResult, triage

# Per-candidate inbox of agent-provided LinkedIn finds, drained each batch.
_inbox: dict[str, list[SourcedCandidate]] = {}


def provide(candidate: str, finds: list[SourcedCandidate]) -> int:
    """Agent worker hands LinkedIn EA finds it triaged in the browser."""
    box = _inbox.setdefault(candidate, [])
    box.extend(finds)
    return len(box)


def pending(candidate: str) -> int:
    return len(_inbox.get(candidate, []))


class LinkedInAgentSourcer:
    label = "linkedin_ea_triage"

    def __init__(self, candidate_key: str):
        self._key = candidate_key

    def fetch(self, profile: Profile, cursor) -> SourcerResult:
        box = _inbox.get(self._key, [])
        _inbox[self._key] = []  # drain
        if not box:
            return SourcerResult(self.label, [], exhausted=True,
                                 error="no agent-provided LinkedIn finds this fire")
        vetted = [c for c in (triage(profile, r) for r in box) if c is not None]
        vetted.sort(key=lambda c: c.fit_score, reverse=True)
        return SourcerResult(self.label, vetted, exhausted=not vetted)
