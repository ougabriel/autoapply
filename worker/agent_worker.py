"""Reference agent worker - the hands of the autonomous loop.

This is the bridge between the orchestrator (the brain's control flow) and the
real submission work. In production an LLM agent with Playwright MCP plays this
role, reading the WAT and driving a logged-in browser. This reference
implementation shows the EXACT loop contract the agent must follow, and is
runnable end-to-end with a pluggable "sourcer" and "submitter" so the
orchestration can be tested without a live browser.

The loop (mirrors WAT Stage 6b 4_batch_loop + Stage 6c drain):
  1. claim a batch (orchestrator.start_batch)            -> stop-first, lock-second
  2. source + triage candidates                          -> the 3-agent fan-out, here a callable
  3. for each candidate, while should_continue:
       evaluate through the pipeline (filters/route/tailor/integrity)
       if Ready: submit via the submitter, record the honest outcome
  4. end_batch -> continue (queued tick) | finished | stopped

Swap `sourcer` and `submitter` for real implementations:
  - sourcer(candidate, cursor) -> list[Candidate]   (LinkedIn EA / boards / sponsor CSV walk)
  - submitter(candidate, plan) -> Outcome           (Playwright fill_* recipe per ATS)
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Iterable

from app import db
from app.services import (
    cv_router,
    event_log,
    filters,
    orchestrator,
    profiles as profiles_svc,
    run_state,
    sponsor_match,
    tailoring,
)


@dataclass
class Candidate:
    company: str
    title: str
    url: str
    ats: str | None = None
    description: str = ""


@dataclass
class Outcome:
    status: str  # Submitted | Skipped-blocked | NeedsUserAction
    confirmation_url: str | None = None
    note: str = ""


Sourcer = Callable[[str, run_state.Cursor], Iterable[Candidate]]
Submitter = Callable[[str, dict], Outcome]


def run_batch(candidate: str, sourcer: Sourcer, submitter: Submitter,
              max_per_batch: int = 10) -> str:
    """Run one batch under the orchestrator contract. Returns the end disposition."""
    profile = profiles_svc.load_profile(candidate)

    claim = orchestrator.start_batch(candidate, source="agent-worker")
    if not claim.started:
        return claim.reason
    batch_id = claim.batch_id

    submitted_this_batch = 0
    try:
        cursor = run_state.load_cursor(candidate)
        candidates = list(sourcer(candidate, cursor))
        event_log.emit(candidate, event_log.KIND_SOURCE,
                       f"Sourced {len(candidates)} candidate(s) this batch.")

        for cand in candidates:
            ok, reason = orchestrator.should_continue(candidate)
            if not ok:
                event_log.emit(candidate, event_log.KIND_BATCH,
                               f"Halting drain: {reason}")
                break
            if submitted_this_batch >= max_per_batch:
                event_log.emit(candidate, event_log.KIND_BATCH,
                               f"Per-batch target {max_per_batch} reached.")
                break

            plan = _evaluate(profile, cand)
            if plan["verdict"] != "Ready":
                # The evaluate path already recorded the skip reason via events;
                # log a non-submitted outcome so the tracker stays honest.
                orchestrator.record_outcome(
                    candidate, status="Skipped-blocked", company=cand.company,
                    title=cand.title, lane=plan.get("lane", "n/a"), ats=cand.ats,
                    note=plan.get("reason", plan["verdict"]),
                )
                continue

            # Hand the ready plan to the submitter (the real browser work).
            outcome = submitter(candidate, plan)
            orchestrator.record_outcome(
                candidate, status=outcome.status, company=cand.company, title=cand.title,
                lane=plan["lane"], ats=cand.ats,
                confirmation_url=outcome.confirmation_url, note=outcome.note,
            )
            if outcome.status == "Submitted":
                submitted_this_batch += 1
    finally:
        disposition = orchestrator.end_batch(candidate, batch_id)

    # Queue-and-continue: if a tick was waiting, run the next batch back-to-back.
    if disposition.get("next") == "continue":
        return run_batch(candidate, sourcer, submitter, max_per_batch)
    return disposition.get("next", "finished")


def _evaluate(profile, cand: Candidate) -> dict:
    """Full pre-submission pipeline for one candidate (filters/route/tailor/integrity)."""
    sponsor_matched = sponsor_match.is_sponsor(cand.company)
    decision = filters.evaluate(profile, cand.company, cand.title, cand.description)
    if not decision.keep:
        return {"verdict": "Skip", "reason": decision.reason}

    lane = cv_router.route(profile, cand.title, cand.description)
    cv_file = cv_router.cv_file_for_lane(profile, lane)
    tailored = tailoring.build_letter(profile, cand.company, cand.title, cand.description, lane)
    if not tailored.gate.ok:
        return {"verdict": "Blocked-by-integrity-gate", "lane": lane,
                "reason": "; ".join(tailored.gate.violations)}

    return {
        "verdict": "Ready",
        "sponsor_matched": sponsor_matched,
        "company": cand.company,
        "title": cand.title,
        "url": cand.url,
        "ats": cand.ats,
        "lane": lane,
        "cv_file": cv_file,
        "matched_strengths": tailored.matched_strengths,
        "letter": tailored.letter,
        "integrity_warnings": tailored.gate.warnings,
    }


# --------------------------------------------------------------------------- #
# A demo sourcer + submitter so the loop is runnable without a live browser.
# Replace these with the real LinkedIn/board sourcer and Playwright submitter.
# --------------------------------------------------------------------------- #
def demo_sourcer(candidate: str, cursor: run_state.Cursor) -> list[Candidate]:
    return [
        Candidate("Barchester Healthcare", "Health Care Assistant",
                  "https://example.com/jobs/1", ats="Greenhouse",
                  description="Person-centred personal care, dementia care, safe moving and handling."),
        Candidate("Cygnet Health Care", "Mental Health Support Worker",
                  "https://example.com/jobs/2", ats="Workable",
                  description="Support people with mental health needs, de-escalation, emotional support."),
    ]


def demo_submitter(candidate: str, plan: dict) -> Outcome:
    # A real submitter drives Playwright through the fill_* recipe for plan['ats'].
    time.sleep(0.2)
    return Outcome(status="Submitted",
                   confirmation_url=f"https://example.com/confirmation/{plan['company']}")


if __name__ == "__main__":
    import sys

    db.init_db()
    who = sys.argv[1] if len(sys.argv) > 1 else "racheal"
    print(f"Running one demo batch for '{who}'...")
    result = run_batch(who, demo_sourcer, demo_submitter)
    print("Batch disposition:", result)
