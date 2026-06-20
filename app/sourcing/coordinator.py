"""Fan-out coordinator (WAT Stage 6c merge_and_drain).

Runs the three sourcers CONCURRENTLY, then merges + dedupes + ranks their output
into a single pre-vetted candidate list for the worker to drain serially.

  step_1 collect  - run all sourcers in parallel (threads; they are I/O bound)
  step_2 dedupe   - cross-source on (company, role-family); keep higher fit_score
  step_3 rank     - sort by fit_score desc
  step_4 (drain)  - done by the agent worker, not here (single-session browser)

Each sourcer failure is isolated: one dead board or empty source never kills the
fan-out (WAT on_fail: proceed with the survivors).
"""
from __future__ import annotations

import concurrent.futures

from ..models import Profile
from ..services import event_log, run_state
from .base import Sourcer, SourcedCandidate


def run_fanout(candidate_key: str, profile: Profile, cursor: run_state.Cursor,
               sourcers: list[Sourcer], *, emit_events: bool = True,
               outcome_boost: dict | None = None) -> list[SourcedCandidate]:
    """Execute the parallel sourcing fan-out and return a merged, ranked list.

    If `outcome_boost` (source -> callback_rate) is given, candidates from sources
    that historically produce interviews are nudged up the ranking - the loop
    learns which channels actually work, not just which are reachable.
    """
    if emit_events:
        event_log.emit(candidate_key, event_log.KIND_SOURCE,
                       f"Fan-out: spawning {len(sourcers)} sourcers in parallel.")

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(sourcers))) as ex:
        future_map = {ex.submit(s.fetch, profile, cursor): s for s in sourcers}
        for future in concurrent.futures.as_completed(future_map):
            sourcer = future_map[future]
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001
                if emit_events:
                    event_log.emit(candidate_key, event_log.KIND_ERROR,
                                   f"Sourcer '{sourcer.label}' failed: {exc}")

    # step_1 collect + per-source reporting
    collected: list[SourcedCandidate] = []
    for r in results:
        collected.extend(r.candidates)
        if emit_events:
            if r.error and not r.candidates:
                event_log.emit(candidate_key, event_log.KIND_SOURCE,
                               f"{r.label}: 0 candidates ({r.error}).")
            else:
                event_log.emit(candidate_key, event_log.KIND_SOURCE,
                               f"{r.label}: {len(r.candidates)} pre-vetted candidate(s).")

    # step_2 dedupe: keep the higher fit_score when the same company+role appears twice
    best: dict[tuple[str, str], SourcedCandidate] = {}
    duplicates = 0
    for cand in collected:
        key = cand.dedup_key()
        existing = best.get(key)
        if existing is None or cand.fit_score > existing.fit_score:
            if existing is not None:
                duplicates += 1
            best[key] = cand
        else:
            duplicates += 1

    # step_3 rank (fit_score, plus a small learned boost from historical callbacks)
    def _rank_key(c: SourcedCandidate) -> float:
        boost = 0.0
        if outcome_boost:
            # source like "direct_boards:greenhouse" -> match on the channel prefix.
            channel = (c.source or "").split(":")[0]
            rate = outcome_boost.get(c.source) or outcome_boost.get(channel) or 0.0
            boost = rate * 2.0  # up to +2.0 for a 100%-callback source
        return c.fit_score + boost

    ranked = sorted(best.values(), key=_rank_key, reverse=True)

    if emit_events:
        event_log.emit(candidate_key, event_log.KIND_SOURCE,
                       f"Merged {len(collected)} -> {len(ranked)} ranked "
                       f"({duplicates} cross-source duplicate(s) dropped).")
    return ranked


def default_sourcers(candidate_key: str, profile=None, fetch_json=None) -> list[Sourcer]:
    """Assemble the fan-out for a candidate, selecting channels by profile/sector.

    The RIGHT CHANNELS matter: care candidates source NHS-TRAC first; tech
    candidates source direct ATS boards first. A profile can override via its
    `channels` list; otherwise sector defaults apply.

    Channel registry:
      direct_boards - public Greenhouse/Workable APIs (needs fetch_json)
      sponsor_walk  - UK sponsor-register CSV walk
      linkedin      - LinkedIn EA agent-hook
      nhs_trac      - NHS Jobs/TRAC agent-hook (care sector)
    """
    from .direct_boards import DirectBoardsSourcer
    from .sponsor_walk import SponsorWalkSourcer
    from .linkedin_agent import LinkedInAgentSourcer
    from .nhs_trac import NhsTracAgentSourcer

    sector = getattr(profile, "sector", "") if profile else ""
    explicit = list(getattr(profile, "channels", []) or []) if profile else []
    channels = explicit or _default_channels(sector)

    builders = {
        "direct_boards": lambda: DirectBoardsSourcer(fetch_json) if fetch_json else None,
        "sponsor_walk": lambda: SponsorWalkSourcer(),
        "linkedin": lambda: LinkedInAgentSourcer(candidate_key),
        "nhs_trac": lambda: NhsTracAgentSourcer(candidate_key),
    }
    sourcers: list[Sourcer] = []
    for ch in channels:
        build = builders.get(ch)
        if build is None:
            continue
        s = build()
        if s is not None:
            sourcers.append(s)
    # Always ensure at least one sourcer exists.
    if not sourcers:
        sourcers.append(SponsorWalkSourcer())
    return sourcers


def _default_channels(sector: str) -> list[str]:
    low = (sector or "").lower()
    if "health" in low or "care" in low or "social" in low:
        return ["nhs_trac", "sponsor_walk", "linkedin"]
    # Tech / general default: direct boards + LinkedIn + sponsor walk.
    return ["direct_boards", "linkedin", "sponsor_walk"]


def http_fetch_json(url: str):
    """Default real HTTP fetcher (httpx). Imported lazily so tests can stay offline."""
    import httpx

    with httpx.Client(timeout=6.0, follow_redirects=True,
                      headers={"User-Agent": "jobapply-AI/1.0"}) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.json()
