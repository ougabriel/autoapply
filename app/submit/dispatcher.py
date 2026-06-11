"""Submit dispatcher - route a plan to the right ATS adapter, honestly.

Implements the WAT ATS scoreboard: adapters that work end-to-end are dispatched;
captcha/anti-bot ATSes are AUTO-SKIPPED (logged Skipped-blocked, never parked for
a human). Unknown ATSes return NeedsUserAction so the agent can decide.
"""
from __future__ import annotations

from .base import SubmitPlan, SubmitResult, SubmitStatus
from .greenhouse import GreenhouseSubmitter

# WAT Stage 4 scoreboard.
AUTO_SKIP_ATS = {
    "lever", "ashby", "icims", "smartrecruiters", "successfactors",
    "dayforce", "taleo", "bespoke", "own-portal",
}
SUPPORTED_ATS = {"greenhouse"}


def _norm(ats: str | None) -> str:
    return (ats or "").strip().lower()


def submit(plan: SubmitPlan, page=None) -> SubmitResult:
    """Dispatch a submission. `page` is a Playwright page for browser-driven adapters."""
    ats = _norm(plan.ats)

    if ats in AUTO_SKIP_ATS:
        return SubmitResult(SubmitStatus.SKIPPED_BLOCKED,
                            note=f"{plan.ats} is captcha/anti-bot blocked; auto-skipped per WAT.")

    if ats == "greenhouse":
        if page is None:
            return SubmitResult(SubmitStatus.NEEDS_USER_ACTION,
                                note="Greenhouse adapter needs a live browser page.")
        return GreenhouseSubmitter(page).submit(plan)

    return SubmitResult(SubmitStatus.NEEDS_USER_ACTION,
                        note=f"No adapter for ATS '{plan.ats}'. Agent should handle in browser.")


def is_supported(ats: str | None) -> bool:
    return _norm(ats) in SUPPORTED_ATS


def is_auto_skip(ats: str | None) -> bool:
    return _norm(ats) in AUTO_SKIP_ATS
