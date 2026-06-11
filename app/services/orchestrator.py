"""Orchestrator - batch lifecycle implementing the WAT Stage 6b contract.

The orchestrator owns the loop's CONTROL flow (when a batch may start, when it
must stop, the queue-and-continue behaviour, the soft daily cap). It does NOT
drive the browser - that is the agent worker's job. The worker polls
`next_task` and reports outcomes via `record_outcome`; the orchestrator decides
whether the loop continues.

This separation is the whole point of Option B: the app is the brain's nervous
system (state, control, visibility); the agent is the hands.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from .. import db
from . import event_log, profiles as profiles_svc, run_state


@dataclass
class StartResult:
    started: bool
    reason: str
    batch_id: str | None = None


def _daily_cap(candidate: str) -> int:
    try:
        profile = profiles_svc.load_profile(candidate)
        return profile.cadence.dailyTarget
    except FileNotFoundError:
        return 10


def start_batch(candidate: str, source: str = "") -> StartResult:
    """Try to start a batch. Honours stop-first, lock-second (WAT on_fire).

    Returns started=False with a reason when the loop should not start (paused,
    a fresh batch already running -> tick queued, or the daily cap is met).
    """
    # 1. stop signal is ALWAYS checked first.
    if run_state.stop_requested(candidate):
        run_state.clear_stop(candidate)
        event_log.emit(candidate, event_log.KIND_BATCH,
                       "Stop signal observed; honoured and cleared. This fire is sacrificed.")
        return StartResult(False, "stop-signal-honoured")

    # 2. lock check second.
    if run_state.lock_is_fresh(candidate):
        run_state.enqueue_tick(candidate)
        event_log.emit(candidate, event_log.KIND_BATCH,
                       "Batch already running; tick queued for pickup.",
                       queue_depth=run_state.queue_depth(candidate))
        return StartResult(False, "lock-fresh-tick-queued")

    # daily cap (soft): if met, write a lock and finish fast.
    cur = run_state.load_cursor(candidate)
    cap = _daily_cap(candidate)
    if cur.today_count >= cap:
        event_log.emit(candidate, event_log.KIND_BATCH,
                       f"Daily cap met ({cur.today_count}/{cap}); no submissions this fire.")
        return StartResult(False, "daily-cap-met")

    # acquire.
    batch_id = uuid.uuid4().hex[:8]
    run_state.acquire_lock(candidate, batch_id, source)
    cur.session_ids.append(batch_id)
    cur.last_source = source or cur.last_source
    run_state.save_cursor(candidate, cur)
    event_log.emit(candidate, event_log.KIND_BATCH, f"Batch {batch_id} started.",
                   batch_id=batch_id, source=source, today_count=cur.today_count, daily_cap=cap)
    return StartResult(True, "started", batch_id)


def should_continue(candidate: str) -> tuple[bool, str]:
    """Called by the worker at each iteration boundary. Stop-signal aware + cap aware."""
    if run_state.stop_requested(candidate):
        return False, "stop-requested"
    cur = run_state.load_cursor(candidate)
    if cur.today_count >= _daily_cap(candidate):
        return False, "daily-cap-met"
    return True, "continue"


def record_outcome(candidate: str, *, status: str, company: str, title: str,
                   lane: str, ats: str | None = None,
                   confirmation_url: str | None = None, job_id: int | None = None,
                   note: str = "") -> dict:
    """Record one application outcome. Submitted outcomes count toward the daily cap."""
    if job_id is None:
        job_id = db.upsert_job(candidate, company, title,
                               url=confirmation_url or f"manual://{company}/{title}",
                               ats=ats)
    db.add_application(candidate, job_id, company, title, lane, status,
                       ats=ats, confirmation_url=confirmation_url, note=note)

    if status == "Submitted":
        cur = run_state.load_cursor(candidate)
        cur.today_count += 1
        run_state.save_cursor(candidate, cur)
        event_log.emit(candidate, event_log.KIND_SUBMIT,
                       f"Submitted -> {company} ({ats or 'unknown ATS'}) [{lane}]",
                       company=company, title=title, lane=lane, ats=ats,
                       confirmation_url=confirmation_url, today_count=cur.today_count)
    elif status == "Skipped-blocked":
        event_log.emit(candidate, event_log.KIND_SKIP,
                       f"Skipped-blocked -> {company}: {note}",
                       company=company, title=title, ats=ats)
    elif status == "NeedsUserAction":
        event_log.emit(candidate, event_log.KIND_NEEDS_USER,
                       f"Needs user action -> {company}: {note}",
                       company=company, title=title, ats=ats)
    return {"recorded": status, "company": company}


def end_batch(candidate: str, batch_id: str) -> dict:
    """End-of-batch: queue-and-continue or clean finish (WAT 4_batch_loop / 6_clean_finish)."""
    # clean stop takes precedence.
    if run_state.stop_requested(candidate):
        run_state.release_lock(candidate)
        run_state.clear_queue(candidate)
        run_state.clear_stop(candidate)
        event_log.emit(candidate, event_log.KIND_BATCH,
                       f"Batch {batch_id} stopped by user; queue drained, cursor kept.")
        return {"next": "stopped"}

    # queue-and-continue: if a tick is waiting, keep going in the same invocation.
    tick = run_state.pop_tick(candidate)
    if tick is not None:
        run_state.refresh_lock(candidate, batch_id)
        event_log.emit(candidate, event_log.KIND_BATCH,
                       f"Queued tick picked up; batch {batch_id} continues back-to-back.",
                       queue_depth=run_state.queue_depth(candidate))
        return {"next": "continue"}

    # clean finish.
    cur = run_state.load_cursor(candidate)
    cur.last_iso = run_state._iso()
    run_state.save_cursor(candidate, cur)
    run_state.release_lock(candidate)
    event_log.emit(candidate, event_log.KIND_BATCH,
                   f"Batch {batch_id} finished cleanly. Today {cur.today_count}/{_daily_cap(candidate)}.",
                   today_count=cur.today_count)
    return {"next": "finished"}


def pause(candidate: str) -> None:
    run_state.request_stop(candidate)
    event_log.emit(candidate, event_log.KIND_BATCH, "Pause requested by user (stop signal written).")


def resume_from_pending(candidate: str) -> StartResult:
    """Operator-driven resume: clear any stale stop and try to start a batch."""
    run_state.clear_stop(candidate)
    return start_batch(candidate, source="resume")
