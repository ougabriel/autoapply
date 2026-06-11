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
               sourcers: list[Sourcer], *, emit_events: bool = True) -> list[SourcedCandidate]:
    """Execute the parallel sourcing fan-out and return a merged, ranked list."""
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

    # step_3 rank
    ranked = sorted(best.values(), key=lambda c: c.fit_score, reverse=True)

    if emit_events:
        event_log.emit(candidate_key, event_log.KIND_SOURCE,
                       f"Merged {len(collected)} -> {len(ranked)} ranked "
                       f"({duplicates} cross-source duplicate(s) dropped).")
    return ranked


def default_sourcers(candidate_key: str, fetch_json=None) -> list[Sourcer]:
    """Assemble the standard three-way fan-out for a candidate.

    - direct boards (deterministic HTTP; needs a fetch_json callable)
    - sponsor-register walk (deterministic CSV walk)
    - LinkedIn EA (agent-hook; drains agent-provided finds)
    """
    from .direct_boards import DirectBoardsSourcer
    from .sponsor_walk import SponsorWalkSourcer
    from .linkedin_agent import LinkedInAgentSourcer

    sourcers: list[Sourcer] = [
        SponsorWalkSourcer(),
        LinkedInAgentSourcer(candidate_key),
    ]
    if fetch_json is not None:
        sourcers.insert(0, DirectBoardsSourcer(fetch_json))
    return sourcers


def http_fetch_json(url: str):
    """Default real HTTP fetcher (httpx). Imported lazily so tests can stay offline."""
    import httpx

    with httpx.Client(timeout=15.0, follow_redirects=True,
                      headers={"User-Agent": "jobapply-AI/1.0"}) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.json()
