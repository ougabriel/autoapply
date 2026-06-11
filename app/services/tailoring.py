"""Tailoring - build an honest cover letter / supporting statement from a template.

This module assembles the letter from the candidate's REAL strengths matched to
the job's stated criteria. It deliberately does NOT invent content. The output is
always passed through the integrity gate before it can be used.

The actual prose generation can later be delegated to an LLM; the contract here
is that whatever produces the text, the result must pass `integrity_gate`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .. import config
from ..models import Profile
from . import integrity_gate


@dataclass
class TailoredApplication:
    lane: str
    matched_strengths: list[str]
    letter: str
    gate: integrity_gate.GateResult


def _load_template() -> str:
    path = config.TEMPLATES_DIR / "supporting_statement_template.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return (
        "Dear Hiring Team,\n\n"
        "I am applying for the {title} role at {company}. {opening}\n\n"
        "{strengths_paragraph}\n\n"
        "{closing}\n\n"
        "Kind regards,\n{name}\n"
    )


def _match_strengths(profile: Profile, description: str, limit: int = 5) -> list[str]:
    """Pick the candidate's real strengths that the JD actually asks for."""
    low = (description or "").lower()
    scored: list[tuple[int, str]] = []
    for strength in profile.skillsTruth.has:
        tokens = [t for t in re.split(r"[\s/]+", strength.lower()) if len(t) > 3]
        score = sum(1 for t in tokens if t in low)
        scored.append((score, strength))
    scored.sort(key=lambda x: x[0], reverse=True)
    chosen = [s for score, s in scored if score > 0][:limit]
    if not chosen:
        # No keyword overlap: lead with the candidate's strongest core strengths.
        chosen = profile.skillsTruth.has[:limit]
    return chosen


def build_letter(profile: Profile, company: str, title: str, description: str, lane: str) -> TailoredApplication:
    strengths = _match_strengths(profile, description)
    template = _load_template()

    strengths_sentence = (
        "In my work I have provided "
        + ", ".join(s.lower() for s in strengths[:-1])
        + (", and " + strengths[-1].lower() if len(strengths) > 1 else strengths[0].lower())
        + "."
    )

    letter = (
        template.replace("{title}", title)
        .replace("{company}", company)
        .replace("{name}", profile.candidate)
        .replace(
            "{opening}",
            "I am drawn to this role because it matches the hands-on care work I do every day.",
        )
        .replace("{strengths_paragraph}", strengths_sentence)
        .replace(
            "{closing}",
            "I would welcome the chance to bring this experience to your team and "
            "to keep supporting people with warmth and care.",
        )
    )

    gate = integrity_gate.check_text(letter, profile, is_prose=True)
    return TailoredApplication(lane=lane, matched_strengths=strengths, letter=letter, gate=gate)
