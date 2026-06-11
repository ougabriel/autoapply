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
from app.sourcing import coordinator, resolver


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
            # Isolated: a submitter that throws must NOT freeze the whole batch -
            # record it honestly and move on (the user always sees what happened).
            try:
                outcome = submitter(candidate, plan)
            except Exception as exc:  # noqa: BLE001
                event_log.emit(candidate, event_log.KIND_ERROR,
                               f"Submit failed for {cand.company}: {exc}")
                outcome = Outcome(status="NeedsUserAction",
                                  note=f"Submitter error: {exc}")
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


def fanout_sourcer(candidate: str, cursor: run_state.Cursor) -> list[Candidate]:
    """Real sourcing: run the parallel fan-out, resolve sponsor leads to real
    vacancies, and return ranked applyable candidates.

    The cursor is advanced by the sponsor-walk sourcer in place; persist it so the
    next batch continues where this one stopped.
    """
    profile = profiles_svc.load_profile(candidate)
    sourcers = coordinator.default_sourcers(candidate, fetch_json=coordinator.http_fetch_json)
    ranked = coordinator.run_fanout(candidate, profile, cursor, sourcers)
    run_state.save_cursor(candidate, cursor)

    applyable: list[Candidate] = []
    resolved_count = 0
    leads_seen = 0
    companies_added: set[str] = set()

    def _company_key(name: str) -> str:
        import re as _re
        return _re.sub(r"[^a-z0-9]", "", name.lower())

    # Pass 1: candidates that ALREADY have a real URL (direct boards, LinkedIn).
    # These need no resolution, so the batch can start submitting immediately.
    leads: list = []
    for c in ranked:
        if _is_lead(c):
            leads.append(c)
            continue
        key = _company_key(c.company)
        if key in companies_added:
            continue
        companies_added.add(key)
        applyable.append(Candidate(company=c.company, title=c.title, url=c.url,
                                    ats=c.ats, description=c.description))

    if applyable:
        event_log.emit(candidate, event_log.KIND_SOURCE,
                       f"{len(applyable)} ready vacancy(ies) with live URLs (no resolution needed).")

    # Pass 2: resolve a bounded number of sponsor leads, emitting progress so the
    # user always sees activity (this is the slow, network-bound part).
    for c in leads:
        if leads_seen >= MAX_LEADS_TO_RESOLVE:
            break
        leads_seen += 1
        event_log.emit(candidate, event_log.KIND_SOURCE,
                       f"Resolving sponsor lead {leads_seen}/{min(len(leads), MAX_LEADS_TO_RESOLVE)}: {c.company}")
        try:
            real = resolver.resolve_company(profile, c.company, coordinator.http_fetch_json)
        except Exception as exc:  # noqa: BLE001 - a slow/dead host must not stall the batch
            event_log.emit(candidate, event_log.KIND_SOURCE,
                           f"Lead {c.company} skipped (resolve error: {str(exc)[:60]}).")
            continue
        real.sort(key=lambda r: r.fit_score, reverse=True)
        for r in real:
            key = _company_key(r.company)
            if key in companies_added:
                continue
            companies_added.add(key)
            resolved_count += 1
            applyable.append(Candidate(company=r.company, title=r.title, url=r.url,
                                       ats=r.ats, description=r.description))
            break  # one role from this company is enough

    if resolved_count:
        event_log.emit(candidate, event_log.KIND_SOURCE,
                       f"Resolved {resolved_count} live vacancy(ies) from sponsor leads.")
    return applyable


def _is_lead(c) -> bool:
    return (not c.url) or "careers page to resolve" in (c.title or "").lower()


# Cap how many sponsor leads we probe per fire (each probe is several HTTP calls).
# Kept small so a fire stays responsive; direct-board candidates (already resolved)
# are submitted first regardless of this cap.
MAX_LEADS_TO_RESOLVE = 8


# --------------------------------------------------------------------------- #
# Demo sourcer + submitter so the loop is runnable without network or a browser.
# Replace demo_submitter with the real Playwright fill_* submitter.
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


def make_playwright_submitter(cv_dir: str):
    """Build a real submitter that drives a logged-in browser per the ATS adapter.

    Opens ONE persistent browser session and reuses it for every submission in the
    batch (serial, single-session per the WAT). Routes each plan through the
    dispatcher: supported ATS -> adapter; captcha ATS -> auto-skip; else NeedsUserAction.
    """
    import os
    from contextlib import ExitStack

    from app.submit import browser, dispatcher
    from app.submit.base import SubmitPlan

    stack = ExitStack()
    page_holder: dict = {}

    def _page():
        if "page" not in page_holder:
            page_holder["page"] = stack.enter_context(browser.browser_session())
        return page_holder["page"]

    def submit(candidate: str, plan: dict) -> Outcome:
        ats = plan.get("ats")
        # Auto-skip blocked ATSes without opening the browser at all.
        if dispatcher.is_auto_skip(ats):
            return Outcome(status="Skipped-blocked",
                           note=f"{ats} is captcha/anti-bot blocked; auto-skipped per WAT.")

        profile = profiles_svc.load_profile(candidate)
        cv_file = plan.get("cv_file") or ""
        cv_path = os.path.join(cv_dir, cv_file) if cv_file else ""
        if cv_file and not os.path.exists(cv_path):
            return Outcome(status="NeedsUserAction",
                           note=f"Routed CV not found on disk: {cv_path}")

        sub_plan = SubmitPlan(
            profile=profile, company=plan["company"], title=plan["title"],
            url=plan["url"], lane=plan["lane"], cv_path=cv_path,
            letter=plan["letter"], ats=ats,
        )

        # Only supported ATSes drive the browser; others never open it.
        if not dispatcher.is_supported(ats):
            result = dispatcher.submit(sub_plan, page=None)
            return Outcome(status=result.status.value,
                           confirmation_url=result.confirmation_url, note=result.note)

        # Open the browser lazily, with a clear error if it cannot launch (e.g. the
        # real Edge is already running and locking the profile). This prevents the
        # batch from freezing on "running" with no feedback.
        try:
            page = _page()
        except Exception as exc:  # noqa: BLE001
            return Outcome(
                status="NeedsUserAction",
                note=("Could not open the logged-in browser: " + str(exc)[:160] +
                      " | Close any open Edge windows using this profile, then retry."),
            )

        result = dispatcher.submit(sub_plan, page=page)
        return Outcome(status=result.status.value,
                       confirmation_url=result.confirmation_url, note=result.note)

    submit.close = stack.close  # caller closes the browser at batch end
    return submit


if __name__ == "__main__":
    import sys

    db.init_db()
    who = sys.argv[1] if len(sys.argv) > 1 else "racheal"
    mode = sys.argv[2] if len(sys.argv) > 2 else "demo"

    if mode == "live":
        # Real fan-out sourcing + real Playwright submission (logged-in browser).
        cv_dir = sys.argv[3] if len(sys.argv) > 3 else "."
        submitter = make_playwright_submitter(cv_dir)
        print(f"Running LIVE batch for '{who}' (cv_dir={cv_dir})...")
        try:
            result = run_batch(who, fanout_sourcer, submitter)
        finally:
            if hasattr(submitter, "close"):
                submitter.close()
    elif mode == "fanout":
        # Real fan-out sourcing, but demo submitter (no browser) - safe dry run.
        print(f"Running fan-out DRY RUN for '{who}' (demo submitter)...")
        result = run_batch(who, fanout_sourcer, demo_submitter)
    else:
        print(f"Running DEMO batch for '{who}' (stub sourcer + submitter)...")
        result = run_batch(who, demo_sourcer, demo_submitter)

    print("Batch disposition:", result)
