"""Batch runner - launches the worker loop in a background thread.

This is the missing link the UI needs: clicking Start must actually RUN a batch,
not just acquire the lock. The runner spawns the worker's run_batch on a daemon
thread so the HTTP request returns immediately while the loop drains in the
background, emitting events to the live feed.

Modes:
  demo   - stub sourcer + stub submitter (no network, no browser). Proves the loop.
  dryrun - real fan-out sourcing + resolver, but stub submitter (no browser). Safe.
  live   - real fan-out + real Playwright submission in the logged-in browser.

Only one batch thread per candidate at a time (the orchestrator lock enforces the
rest of the contract).
"""
from __future__ import annotations

import threading

from . import event_log, run_state

# candidate -> running thread
_threads: dict[str, threading.Thread] = {}


def is_running(candidate: str) -> bool:
    t = _threads.get(candidate)
    return t is not None and t.is_alive()


def start(candidate: str, mode: str = "demo", cv_dir: str = ".") -> dict:
    """Launch a batch in the background. Returns immediately."""
    if is_running(candidate):
        return {"started": False, "reason": "batch-thread-already-running"}

    # Import here to avoid a heavy import at app startup.
    from worker import agent_worker

    def _run():
        try:
            if mode == "live":
                submitter = agent_worker.make_playwright_submitter(cv_dir)
                try:
                    agent_worker.run_batch(candidate, agent_worker.fanout_sourcer, submitter)
                finally:
                    if hasattr(submitter, "close"):
                        submitter.close()
            elif mode == "dryrun":
                agent_worker.run_batch(candidate, agent_worker.fanout_sourcer,
                                       agent_worker.demo_submitter)
            else:  # demo
                agent_worker.run_batch(candidate, agent_worker.demo_sourcer,
                                       agent_worker.demo_submitter)
        except Exception as exc:  # noqa: BLE001 - surface failures to the feed, never crash silently
            event_log.emit(candidate, event_log.KIND_ERROR, f"Batch crashed: {exc}")
            # Leave the lock for the next fire to treat as stale (WAT on abort).
        finally:
            _threads.pop(candidate, None)

    t = threading.Thread(target=_run, name=f"batch-{candidate}", daemon=True)
    _threads[candidate] = t
    event_log.emit(candidate, event_log.KIND_BATCH,
                   f"Start requested (mode={mode}). Launching batch worker.")
    t.start()
    return {"started": True, "reason": "launched", "mode": mode}
