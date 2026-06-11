"""Local SQLite persistence - single user, no server required.

Tables are intentionally simple. The Profile truth still lives in the JSON files
under profiles/ (human-editable); the DB tracks jobs and applications (run state).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate       TEXT NOT NULL,
    company         TEXT NOT NULL,
    title           TEXT NOT NULL,
    url             TEXT NOT NULL,
    ats             TEXT,
    source          TEXT,
    sponsor_matched INTEGER DEFAULT 0,
    description     TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    UNIQUE(candidate, url)
);

CREATE TABLE IF NOT EXISTS applications (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate        TEXT NOT NULL,
    job_id           INTEGER NOT NULL,
    company          TEXT NOT NULL,
    title            TEXT NOT NULL,
    lane             TEXT NOT NULL,
    status           TEXT NOT NULL,
    ats              TEXT,
    confirmation_url TEXT,
    note             TEXT DEFAULT '',
    created_at       TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);

CREATE INDEX IF NOT EXISTS idx_apps_candidate ON applications(candidate);
CREATE INDEX IF NOT EXISTS idx_apps_company ON applications(candidate, company);
CREATE INDEX IF NOT EXISTS idx_jobs_candidate ON jobs(candidate);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(_SCHEMA)


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #
def upsert_job(
    candidate: str,
    company: str,
    title: str,
    url: str,
    ats: Optional[str] = None,
    source: Optional[str] = None,
    sponsor_matched: bool = False,
    description: str = "",
) -> int:
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO jobs (candidate, company, title, url, ats, source,
                              sponsor_matched, description, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate, url) DO UPDATE SET
                company=excluded.company,
                title=excluded.title,
                ats=excluded.ats,
                sponsor_matched=excluded.sponsor_matched,
                description=excluded.description
            """,
            (candidate, company, title, url, ats, source,
             int(sponsor_matched), description, _now()),
        )
        if cur.lastrowid:
            return cur.lastrowid
        row = conn.execute(
            "SELECT id FROM jobs WHERE candidate=? AND url=?", (candidate, url)
        ).fetchone()
        return row["id"]


def list_jobs(candidate: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE candidate=? ORDER BY id DESC", (candidate,)
        ).fetchall()
        return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Applications
# --------------------------------------------------------------------------- #
def add_application(
    candidate: str,
    job_id: int,
    company: str,
    title: str,
    lane: str,
    status: str,
    ats: Optional[str] = None,
    confirmation_url: Optional[str] = None,
    note: str = "",
) -> int:
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO applications (candidate, job_id, company, title, lane,
                                      status, ats, confirmation_url, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (candidate, job_id, company, title, lane, status, ats,
             confirmation_url, note, _now()),
        )
        return cur.lastrowid


def list_applications(candidate: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM applications WHERE candidate=? ORDER BY id DESC",
            (candidate,),
        ).fetchall()
        return [dict(r) for r in rows]


def recent_company_applications(candidate: str, company: str, since_iso: str) -> list[dict]:
    """Applications to a company since a cutoff - powers the 90-day dedup gate (L7/L14)."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM applications
            WHERE candidate=? AND lower(company)=lower(?) AND created_at >= ?
            ORDER BY created_at DESC
            """,
            (candidate, company, since_iso),
        ).fetchall()
        return [dict(r) for r in rows]
