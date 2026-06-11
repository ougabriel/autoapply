"""Structured event log - the live activity feed the user watches while running.

Events are appended to a per-candidate JSONL file and also broadcast to any
in-process subscribers (the SSE endpoint). This is what turns the loop from a
black box into something observable: every source, skip, submit, and integrity
block becomes a visible line.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from . import run_state

# event "kind" vocabulary (keep small and honest)
KIND_INFO = "info"
KIND_SOURCE = "source"
KIND_FILTER = "filter"
KIND_ROUTE = "route"
KIND_INTEGRITY = "integrity"
KIND_SUBMIT = "submit"
KIND_SKIP = "skip"
KIND_NEEDS_USER = "needs_user"
KIND_BATCH = "batch"
KIND_ERROR = "error"

# In-process subscribers: candidate -> set of (queue, loop). The loop is captured
# at subscribe time so a batch running in a BACKGROUND THREAD can deliver events
# to the SSE generator (which lives on the asyncio event loop) thread-safely.
_subscribers: dict[str, set[tuple[asyncio.Queue, "asyncio.AbstractEventLoop"]]] = {}


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_path(candidate: str):
    return run_state.state_dir(candidate) / "events.jsonl"


def emit(candidate: str, kind: str, message: str, **fields: Any) -> dict:
    """Record one event: append to JSONL and broadcast to live subscribers.

    Safe to call from any thread: delivery to each subscriber is marshalled onto
    that subscriber's event loop via call_soon_threadsafe.
    """
    event = {"ts": _iso(), "candidate": candidate, "kind": kind, "message": message}
    if fields:
        event.update(fields)

    with _log_path(candidate).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")

    for q, loop in list(_subscribers.get(candidate, set())):
        try:
            loop.call_soon_threadsafe(_safe_put, q, event)
        except RuntimeError:
            # Loop is closed/gone; drop this subscriber silently.
            pass
    return event


def _safe_put(q: asyncio.Queue, event: dict) -> None:
    try:
        q.put_nowait(event)
    except asyncio.QueueFull:
        pass


def tail(candidate: str, limit: int = 200) -> list[dict]:
    """Return the most recent events (oldest-first within the returned window)."""
    path = _log_path(candidate)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[dict] = []
    for line in lines[-limit:]:
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def subscribe(candidate: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    loop = asyncio.get_event_loop()
    _subscribers.setdefault(candidate, set()).add((q, loop))
    return q


def unsubscribe(candidate: str, q: asyncio.Queue) -> None:
    subs = _subscribers.get(candidate)
    if not subs:
        return
    for pair in list(subs):
        if pair[0] is q:
            subs.discard(pair)
