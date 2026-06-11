"""CV-lane routing - choose the best-fit tailored CV from a job title/spec.

Mirrors the profile.cvRouting rules: an ordered list of {match: regex, lane}.
First match wins; the last rule is the catch-all.
"""
from __future__ import annotations

import re

from ..models import Profile


def route(profile: Profile, title: str, spec: str = "") -> str:
    """Return the CV lane key for a job. Falls back to the last routing rule."""
    haystack = f"{title}\n{spec}"
    for rule in profile.cvRouting:
        try:
            if re.search(rule.match, haystack):
                return rule.lane
        except re.error:
            # A malformed regex in the profile should not crash the loop.
            continue
    # Catch-all: last rule's lane, else first declared lane, else 'default'.
    if profile.cvRouting:
        return profile.cvRouting[-1].lane
    if profile.cvLanes:
        return next(iter(profile.cvLanes))
    return "default"


def cv_file_for_lane(profile: Profile, lane: str) -> str | None:
    """Return the CV filename for a lane (e.g. 'cv_hca.pdf'), if defined."""
    raw = profile.cvLanes.get(lane)
    if not raw:
        return None
    # Profiles sometimes store "cv_hca.pdf - description"; take the filename token.
    return raw.split()[0].strip()
