"""Sponsor matching - match an employer against the Home Office sponsor register.

This is a core differentiator for migrant job seekers. The register
(uk_sponsors.csv, ~140k rows) is loaded once and matched by normalized name.

Matching is deliberately conservative: exact normalized match, then a contained
match on the distinctive part of the org name. The goal is "can this employer
sponsor?" - a false negative just means we treat the JD's own wording as the
signal, never a hard skip.
"""
from __future__ import annotations

import csv
import re
from functools import lru_cache

from .. import config

_COMMON_SUFFIXES = {
    "ltd", "limited", "llp", "plc", "uk", "group", "holdings", "services",
    "care", "healthcare", "homes", "the", "and", "&", "co", "company",
}


def _normalize(name: str) -> str:
    name = (name or "").lower()
    name = re.sub(r"[^a-z0-9\s]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _key_tokens(name: str) -> frozenset[str]:
    return frozenset(
        t for t in _normalize(name).split() if t and t not in _COMMON_SUFFIXES
    )


@lru_cache(maxsize=1)
def _load_register() -> tuple[set[str], list[frozenset[str]]]:
    """Load the sponsor register. Returns (normalized exact names, token sets).

    Cached for the process lifetime. Returns empties if the file is absent so the
    app still runs before the register has been copied in.
    """
    exact: set[str] = set()
    token_sets: list[frozenset[str]] = []
    path = config.SPONSOR_CSV
    if not path.exists():
        return exact, token_sets

    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        name_col = None
        if reader.fieldnames:
            for col in reader.fieldnames:
                if "organisation name" in col.lower() or "organization name" in col.lower():
                    name_col = col
                    break
            if name_col is None:
                name_col = reader.fieldnames[0]
        for row in reader:
            raw = row.get(name_col, "") if name_col else ""
            norm = _normalize(raw)
            if not norm:
                continue
            exact.add(norm)
            token_sets.append(_key_tokens(raw))
    return exact, token_sets


def register_loaded() -> bool:
    exact, _ = _load_register()
    return len(exact) > 0


def is_sponsor(employer: str) -> bool:
    """True if the employer plausibly appears on the sponsor register."""
    exact, token_sets = _load_register()
    if not exact:
        return False

    norm = _normalize(employer)
    if not norm:
        return False
    if norm in exact:
        return True

    # Distinctive-token containment: the employer's key tokens are a subset of a
    # register entry's key tokens (handles "Barchester" vs "Barchester Healthcare Ltd").
    emp_tokens = _key_tokens(employer)
    if not emp_tokens:
        return False
    for reg_tokens in token_sets:
        if emp_tokens and emp_tokens.issubset(reg_tokens):
            return True
        if reg_tokens and reg_tokens.issubset(emp_tokens):
            return True
    return False
