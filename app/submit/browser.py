"""Persistent browser session for submission (logged-in Edge profile).

Mirrors the parent .mcp.json: a persistent user-data-dir so LinkedIn/NHS/ATS
logins survive between runs. Headed by default so the user can watch and step in
for 2FA/captcha. Single session only - submission is always serial.

Usage:
    with browser_session() as page:
        result = dispatcher.submit(plan, page=page)

Playwright is imported lazily so the rest of the app (and the offline tests) do
not require a browser to be installed.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

# Default persistent profile dir (separate from the MCP profiles to avoid locks).
DEFAULT_PROFILE_DIR = Path(
    os.environ.get(
        "JOBAPPLY_BROWSER_PROFILE",
        str(Path(os.environ.get("LOCALAPPDATA", Path.home())) / "jobapply-ai-edge-profile"),
    )
)


@contextmanager
def browser_session(profile_dir: Path | None = None, headless: bool = False, channel: str = "msedge"):
    """Yield a Playwright page backed by a persistent, logged-in browser profile."""
    from playwright.sync_api import sync_playwright

    profile_dir = profile_dir or DEFAULT_PROFILE_DIR
    profile_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            channel=channel,
            headless=headless,
            viewport={"width": 1440, "height": 900},
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            yield page
        finally:
            context.close()
