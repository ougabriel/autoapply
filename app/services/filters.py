"""Stage 2 filtering + the 90-day dedup gate.

Encodes the WAT filter rules: explicit no-sponsorship skip, doNotApply patterns,
qualification gating, and the per-company 90-day dedup (L7/L14).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .. import config, db
from ..models import Profile

_NO_SPONSORSHIP = re.compile(
    r"no\s+sponsorship|cannot\s+sponsor|unable\s+to\s+sponsor|"
    r"must\s+(?:already\s+)?have\s+(?:the\s+)?(?:unrestricted\s+)?right\s+to\s+work|"
    r"no\s+visa",
    re.IGNORECASE,
)


@dataclass
class FilterDecision:
    keep: bool
    reason: str = ""


def passes_sponsorship(description: str) -> FilterDecision:
    if _NO_SPONSORSHIP.search(description or ""):
        return FilterDecision(False, "JD explicitly states no sponsorship.")
    return FilterDecision(True)


def passes_do_not_apply(profile: Profile, title: str, description: str) -> FilterDecision:
    haystack = f"{title}\n{description}".lower()
    for pattern in profile.doNotApply:
        # doNotApply entries are human-readable; match on their distinctive tokens.
        tokens = [t for t in re.split(r"[\s/]+", pattern.lower()) if len(t) > 4]
        hits = sum(1 for t in tokens[:6] if t in haystack)
        # Require a couple of distinctive hits to avoid over-filtering on a single common word.
        if len(tokens) >= 2 and hits >= 2:
            return FilterDecision(False, f"Matches doNotApply rule: '{pattern[:60]}'.")
    return FilterDecision(True)


def passes_qualification_gate(profile: Profile, description: str) -> FilterDecision:
    """Skip roles that hard-require a qualification the candidate lacks."""
    low = (description or "").lower()
    for missing in profile.skillsTruth.doesNotYetHave:
        signal = missing.lower().split("(")[0].strip()
        tokens = [t for t in re.split(r"[\s/]+", signal) if len(t) > 2]
        for tok in tokens[:2]:
            # "required" / "essential" near the missing qualification token.
            if re.search(rf"\b{re.escape(tok)}\b", low) and re.search(
                r"required|essential|must have|minimum", low
            ):
                return FilterDecision(
                    False, f"JD appears to hard-require '{missing}' (not held)."
                )
    return FilterDecision(True)


def passes_dedup(profile: Profile, company: str) -> FilterDecision:
    """90-day per-company dedup (L7). The tracker/DB is the source of truth (L14)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=config.DEDUP_WINDOW_DAYS)).isoformat()
    prior = db.recent_company_applications(profile.candidate, company, cutoff)
    submitted = [p for p in prior if p["status"] in ("Submitted", "Interview", "Offer")]
    if submitted:
        return FilterDecision(
            False,
            f"Already applied to {company} within {config.DEDUP_WINDOW_DAYS} days "
            f"(lane '{submitted[0]['lane']}').",
        )
    return FilterDecision(True)


def evaluate(profile: Profile, company: str, title: str, description: str) -> FilterDecision:
    """Run all Stage 2 filters in order; first failure wins."""
    for check in (
        passes_sponsorship(description),
        passes_do_not_apply(profile, title, description),
        passes_qualification_gate(profile, description),
        passes_dedup(profile, company),
    ):
        if not check.keep:
            return check
    return FilterDecision(True, "Passes all Stage 2 filters.")
