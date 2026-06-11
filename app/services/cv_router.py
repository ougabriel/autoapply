"""CV-lane routing - choose the best-fit tailored CV from a job title/spec.

Mirrors the profile.cvRouting rules: an ordered list of {match: regex, lane}.
First match wins; the last rule is the catch-all.
"""
from __future__ import annotations

import re

from ..models import Profile


def route(profile: Profile, title: str, spec: str = "") -> str:
    """Return the CV lane key for a job.

    Routing is TITLE-FIRST (mirrors the WAT `route_cv_by_role_title`): the job
    title decides the lane. The spec is only consulted as a fallback when no
    routing rule matches the title, so a stray word in the job body (e.g.
    "reliability" inside a DevOps JD) cannot hijack the lane.
    """
    # Pass 1: title only.
    lane = _first_match(profile, title)
    if lane is not None:
        return lane
    # Pass 2: title + spec, for cases where the title is generic/empty.
    lane = _first_match(profile, f"{title}\n{spec}")
    if lane is not None:
        return lane
    # Catch-all: last rule's lane, else first declared lane, else 'default'.
    if profile.cvRouting:
        return profile.cvRouting[-1].lane
    if profile.cvLanes:
        return next(iter(profile.cvLanes))
    return "default"


def _first_match(profile: Profile, haystack: str) -> str | None:
    """First routing rule whose regex matches, excluding the catch-all '.*'."""
    for rule in profile.cvRouting:
        if rule.match.strip() in (".*", "(?i).*"):
            continue
        try:
            if re.search(rule.match, haystack):
                return rule.lane
        except re.error:
            # A malformed regex in the profile should not crash the loop.
            continue
    return None


def cv_file_for_lane(profile: Profile, lane: str) -> str | None:
    """Return the CV filename for a lane (e.g. 'cv_hca.pdf'), if defined."""
    raw = profile.cvLanes.get(lane)
    if not raw:
        return None
    # Profiles sometimes store "cv_hca.pdf - description"; take the filename token.
    return raw.split()[0].strip()
