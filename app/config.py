"""Central paths and settings for the local app.

Everything is resolved relative to the project root so the app runs the same
regardless of the working directory it is launched from.
"""
from __future__ import annotations

import os
from pathlib import Path

# Project root = parent of the `app` package directory.
ROOT = Path(__file__).resolve().parent.parent

PROFILES_DIR = ROOT / "profiles"
TEMPLATES_DIR = ROOT / "templates"
DATA_DIR = ROOT / "data"
STATIC_DIR = ROOT / "app" / "static"
WAT_FILE = ROOT / "job_apply_workflow.wat"

# Local data artefacts (gitignored).
DB_PATH = DATA_DIR / "jobapply.db"
SPONSOR_CSV = DATA_DIR / "uk_sponsors.csv"
PROGRESS_FILE = DATA_DIR / "progress.json"
TRACKER_MD = DATA_DIR / "applications_tracker.md"
APPLY_LOG = DATA_DIR / "apply_log.txt"
APPLIED_COMPANIES = DATA_DIR / ".applied_companies.txt"

# Server.
HOST = os.environ.get("JOBAPPLY_HOST", "127.0.0.1")
PORT = int(os.environ.get("JOBAPPLY_PORT", "8765"))

# Dedup window from the WAT (L7): one lane per company per 90 days.
DEDUP_WINDOW_DAYS = 90


def ensure_dirs() -> None:
    """Create the local directories the app writes to."""
    for d in (PROFILES_DIR, TEMPLATES_DIR, DATA_DIR, STATIC_DIR):
        d.mkdir(parents=True, exist_ok=True)
