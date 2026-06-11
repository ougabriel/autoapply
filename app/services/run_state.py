"""Run-state contract - the lock / queue / cursor / stop-signal machinery.

This is the generalized, per-candidate form of WAT Stage 6b (auto_loop_hourly).
The original used flat dotfiles in the lab dir; here each candidate gets a state
directory under data/runs/<candidate>/ so the loop is multi-candidate safe.

Files (per candidate):
  lock.json            - present while a batch is running (ISO ts + batch id)
  queue.txt            - newline ISO timestamps; each = one tick awaiting pickup
  cursor.json          - last-known position (today, today_count, sources, rows)
  stop_now             - empty file; presence = user wants to pause/stop
  pending_batches.log  - append-only; each line = one batch owed (from scheduler)

All functions are small and idempotent so both the orchestrator and the OS-level
tick script can operate on the same files without coordination races.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .. import config

LOCK_STALE_MINUTES = 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso() -> str:
    return _now().isoformat()


def _today() -> str:
    return _now().strftime("%Y-%m-%d")


def state_dir(candidate: str) -> Path:
    d = config.DATA_DIR / "runs" / candidate
    d.mkdir(parents=True, exist_ok=True)
    return d


def _p(candidate: str, name: str) -> Path:
    return state_dir(candidate) / name


# --------------------------------------------------------------------------- #
# Cursor
# --------------------------------------------------------------------------- #
@dataclass
class Cursor:
    today: str = field(default_factory=_today)
    today_count: int = 0
    last_iso: str = ""
    last_source: str = ""
    last_keyword_index: int = 0
    last_sponsor_row: int = 0
    session_ids: list[str] = field(default_factory=list)


def load_cursor(candidate: str) -> Cursor:
    path = _p(candidate, "cursor.json")
    if not path.exists():
        return Cursor()
    data = json.loads(path.read_text(encoding="utf-8"))
    cur = Cursor(**{k: data.get(k, getattr(Cursor(), k)) for k in Cursor().__dict__})
    # Roll over the daily count if the date changed.
    if cur.today != _today():
        cur.today = _today()
        cur.today_count = 0
    return cur


def save_cursor(candidate: str, cur: Cursor) -> None:
    _p(candidate, "cursor.json").write_text(
        json.dumps(asdict(cur), indent=2), encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# Lock
# --------------------------------------------------------------------------- #
def lock_info(candidate: str) -> dict | None:
    path = _p(candidate, "lock.json")
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def lock_age_minutes(candidate: str) -> float | None:
    info = lock_info(candidate)
    if not info:
        return None
    try:
        t = datetime.fromisoformat(info["iso"])
        return (_now() - t).total_seconds() / 60.0
    except (KeyError, ValueError):
        return None


def lock_is_fresh(candidate: str) -> bool:
    age = lock_age_minutes(candidate)
    return age is not None and age < LOCK_STALE_MINUTES


def acquire_lock(candidate: str, batch_id: str, source: str = "") -> None:
    _p(candidate, "lock.json").write_text(
        json.dumps({"iso": _iso(), "batch_id": batch_id, "source": source}, indent=2),
        encoding="utf-8",
    )


def refresh_lock(candidate: str, batch_id: str, source: str = "") -> None:
    acquire_lock(candidate, batch_id, source)


def release_lock(candidate: str) -> None:
    path = _p(candidate, "lock.json")
    if path.exists():
        path.unlink()


# --------------------------------------------------------------------------- #
# Queue
# --------------------------------------------------------------------------- #
def enqueue_tick(candidate: str) -> None:
    with _p(candidate, "queue.txt").open("a", encoding="utf-8") as fh:
        fh.write(_iso() + "\n")


def queue_depth(candidate: str) -> int:
    path = _p(candidate, "queue.txt")
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def pop_tick(candidate: str) -> str | None:
    """Pop the oldest queued tick. Returns it, or None if the queue is empty."""
    path = _p(candidate, "queue.txt")
    if not path.exists():
        return None
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        return None
    first, rest = lines[0], lines[1:]
    path.write_text(("\n".join(rest) + ("\n" if rest else "")), encoding="utf-8")
    return first


def clear_queue(candidate: str) -> None:
    path = _p(candidate, "queue.txt")
    if path.exists():
        path.write_text("", encoding="utf-8")


# --------------------------------------------------------------------------- #
# Stop signal
# --------------------------------------------------------------------------- #
def request_stop(candidate: str) -> None:
    _p(candidate, "stop_now").write_text("", encoding="utf-8")


def stop_requested(candidate: str) -> bool:
    return _p(candidate, "stop_now").exists()


def clear_stop(candidate: str) -> None:
    path = _p(candidate, "stop_now")
    if path.exists():
        path.unlink()


# --------------------------------------------------------------------------- #
# Pending batches (OS scheduler backlog)
# --------------------------------------------------------------------------- #
def log_pending_batch(candidate: str, batch_id: str, note: str = "tick") -> None:
    with _p(candidate, "pending_batches.log").open("a", encoding="utf-8") as fh:
        fh.write(f"{_iso()}|{batch_id}|{note}\n")


# --------------------------------------------------------------------------- #
# Status snapshot for the UI
# --------------------------------------------------------------------------- #
def status(candidate: str, daily_cap: int) -> dict:
    cur = load_cursor(candidate)
    info = lock_info(candidate)
    age = lock_age_minutes(candidate)
    if stop_requested(candidate):
        loop_state = "paused"
    elif info and lock_is_fresh(candidate):
        loop_state = "running"
    elif queue_depth(candidate) > 0:
        loop_state = "queued"
    else:
        loop_state = "idle"
    return {
        "candidate": candidate,
        "loop_state": loop_state,
        "batch_id": info.get("batch_id") if info else None,
        "lock_age_minutes": round(age, 1) if age is not None else None,
        "lock_stale": (age is not None and age >= LOCK_STALE_MINUTES),
        "queue_depth": queue_depth(candidate),
        "today": cur.today,
        "today_count": cur.today_count,
        "daily_cap": daily_cap,
        "daily_cap_met": cur.today_count >= daily_cap,
        "last_source": cur.last_source,
        "last_sponsor_row": cur.last_sponsor_row,
    }
