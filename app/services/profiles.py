"""Load and list candidate profiles from profiles/*.json.

Profiles are the human-editable truth source. They are validated against the
Pydantic Profile model on load, so a malformed profile fails loudly and early.
"""
from __future__ import annotations

import json

from .. import config
from ..models import Profile


def list_profiles() -> list[str]:
    """Return available profile names (filename stems)."""
    if not config.PROFILES_DIR.exists():
        return []
    return sorted(p.stem for p in config.PROFILES_DIR.glob("*.json"))


def load_profile(name: str) -> Profile:
    """Load and validate a profile by name (filename stem)."""
    path = config.PROFILES_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Profile not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return _coerce(raw)


def _coerce(raw: dict) -> Profile:
    """Adapt the existing *_profile.json shape to the Profile model.

    The existing files use slightly richer shapes (e.g. cvRouting as a list of
    dicts already; skillsTruth with extra keys). Pydantic ignores unknown keys
    where the model permits, and the required keys line up.
    """
    return Profile.model_validate(raw)
