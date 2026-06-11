"""Careers-page resolver - turns a sponsor lead into real, applyable vacancies.

A sponsor-walk lead is just a company name confirmed on the register. To apply we
need its live roles. Many employers run a hosted ATS board whose URL is derived
from a token (a slug of the company name). We DETERMINISTICALLY probe the public
Greenhouse and Workable board APIs with a few token guesses; if a board responds,
its vacancies are parsed and triaged exactly like the direct-boards sourcer.

This stays honest: we only ever return vacancies the ATS actually returned. If no
board is found, the lead is left unresolved (NeedsResolution) rather than guessed.
A bespoke/own-portal employer simply yields nothing here and is left for the agent
to resolve in the browser if it is worth it.
"""
from __future__ import annotations

import re
from typing import Callable

from ..models import Profile
from .base import SourcedCandidate, triage
from .direct_boards import (
    _greenhouse_url,
    _workable_url,
    _parse_greenhouse,
    _parse_workable,
)

FetchJson = Callable[[str], dict | list]


def token_guesses(company: str, limit: int = 4) -> list[str]:
    """Plausible ATS tokens from a company name, most-likely first.

    "Barchester Healthcare Ltd" -> ["barchesterhealthcare", "barchester", ...]
    Common suffixes (ltd, healthcare, care, group) are stripped to form shorter guesses.
    """
    base = company.lower()
    base = re.sub(r"\bt/a\b.*$", "", base)  # drop "trading as ..."
    base = re.sub(r"\(.*?\)", " ", base)     # drop parentheticals
    base = re.sub(r"[^a-z0-9\s]", " ", base)
    words = [w for w in base.split() if w]

    drop = {"ltd", "limited", "llp", "plc", "uk", "the", "and", "co", "company",
            "group", "holdings", "services", "trade", "name"}
    core = [w for w in words if w not in drop]

    guesses: list[str] = []
    if core:
        guesses.append("".join(core))          # full slug, no suffixes
        guesses.append(core[0])                 # first distinctive word
        if len(core) >= 2:
            guesses.append("".join(core[:2]))   # first two words
    guesses.append("".join(words))              # everything, as a fallback

    # De-dupe, keep order, drop too-short tokens.
    seen: set[str] = set()
    out: list[str] = []
    for g in guesses:
        if len(g) >= 3 and g not in seen:
            seen.add(g)
            out.append(g)
    return out[:limit]


def _distinctive_tokens(name: str) -> set[str]:
    drop = {"ltd", "limited", "llp", "plc", "uk", "the", "and", "co", "company",
            "group", "holdings", "services", "trade", "name", "care", "health",
            "healthcare", "homecare", "home", "care", "ltd"}
    base = re.sub(r"\(.*?\)", " ", name.lower())
    base = re.sub(r"\bt/a\b.*$", "", base)
    base = re.sub(r"[^a-z0-9\s]", " ", base)
    return {w for w in base.split() if len(w) > 3 and w not in drop}


def _board_matches_lead(lead_company: str, board_jobs: list[SourcedCandidate]) -> bool:
    """True if the board's company name shares a distinctive token with the lead.

    Guards against guessed-slug collisions where an unrelated company owns the
    Greenhouse/Workable tenant for the token we tried.
    """
    lead_tokens = _distinctive_tokens(lead_company)
    if not lead_tokens:
        # No distinctive token to verify against (e.g. "Haven Care") -> do not trust.
        return False
    for job in board_jobs:
        board_tokens = _distinctive_tokens(job.company)
        if lead_tokens & board_tokens:
            return True
    return False


def resolve_company(profile: Profile, company: str, fetch_json: FetchJson,
                    max_tokens: int = 4) -> list[SourcedCandidate]:
    """Probe known ATS boards for a company and return triaged real vacancies.

    Guards against false matches: a board only counts if the token is distinctive
    (>= 5 chars - short tokens like "haven"/"acme" collide with unrelated boards),
    and resolved vacancies are de-duplicated by URL so a board is never counted twice.
    """
    found: list[SourcedCandidate] = []
    seen_urls: set[str] = set()

    for token in token_guesses(company, limit=max_tokens):
        # Distinctiveness guard: short tokens match unrelated companies' boards.
        if len(token) < 5:
            continue
        for kind, url_fn, parse in (
            ("greenhouse", _greenhouse_url, _parse_greenhouse),
            ("workable", _workable_url, _parse_workable),
        ):
            try:
                payload = fetch_json(url_fn(token))
            except Exception:  # noqa: BLE001 - a miss/404 is expected and fine
                continue
            raw = parse(token, payload)
            if not raw:
                continue
            # Name-similarity guard: the board's own company name must share a
            # distinctive token with the lead, else it is an unrelated tenant
            # that happened to match the guessed slug.
            if not _board_matches_lead(company, raw):
                continue
            for r in raw:
                if r.url in seen_urls:
                    continue
                seen_urls.add(r.url)
                # Keep the register name so sponsor matching + dedup stay consistent.
                r.company = company
                r.source = f"resolved:{kind}"
                found.append(r)
        if found:
            break  # first token that yields a real board wins

    return [c for c in (triage(profile, r) for r in found) if c is not None]
