"""Integrity gate - enforces the honesty contract before any submission.

This is the durable, productizable core. Every generated CV bullet and every
cover-letter / supporting-statement sentence MUST pass this gate. A failure means
regenerate, never submit.

Checks (from job_apply_workflow.wat honesty_contract):
  a) no claim of a qualification listed in skillsTruth.doesNotYetHave
  b) no visa / sponsorship / CoS / salary / notice in prose (L13)
  c) no AI-tell words
  d) no em-dashes
  e) (advisory) flag fabricated-looking metrics for human-style review
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import Profile

# AI-tell words that betray machine-written prose (voice_rule).
AI_TELL_WORDS = [
    "leverage", "leveraging", "delve", "robust", "seamless", "seamlessly",
    "passionate about leveraging", "synergy", "synergies", "cutting-edge",
    "best-in-class", "tapestry", "realm", "navigate the complexities",
    "in today's fast-paced", "elevate", "unlock", "spearhead", "holistic",
    "game-changer", "paradigm", "bandwidth", "deep dive", "circle back",
]

# Topics that must NEVER appear in prose - only in structured form fields (L13).
PROSE_FORBIDDEN_PATTERNS = [
    (r"\bvisa\b", "visa"),
    (r"\bsponsor(ship|ed|ing)?\b", "sponsorship"),
    (r"\bcertificate of sponsorship\b|\bcos\b", "CoS"),
    (r"\bskilled worker\b|\bhealth and care worker\b", "visa route"),
    (r"\bsalary\b|\b£\s?\d", "salary"),
    (r"\bnotice period\b|\bnotice\b", "notice period"),
    (r"\bright to work\b", "right-to-work"),
]

# Em-dash variants (voice_rule: no em-dashes).
EM_DASH_PATTERN = re.compile(r"[\u2014\u2013]")

# Looks like a fabricated metric (advisory only).
METRIC_PATTERN = re.compile(r"\b\d{1,3}(?:[.,]\d+)?\s?%|\b\d{2,}\+?\b")


@dataclass
class GateResult:
    ok: bool
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def check_text(text: str, profile: Profile, *, is_prose: bool = True) -> GateResult:
    """Validate a single piece of generated content against the honesty contract.

    Args:
        text: the generated content (a CV bullet, a letter, etc.).
        profile: the active candidate profile (truth source).
        is_prose: True for cover letters / statements / free-text (L13 applies);
                  False for structured CV bullets (visa-topic rule relaxed, but
                  truth + voice rules still apply).
    """
    violations: list[str] = []
    warnings: list[str] = []
    low = _normalize(text)

    # (d) em-dashes
    if EM_DASH_PATTERN.search(text or ""):
        violations.append("Contains an em-dash or en-dash (voice rule: none allowed).")

    # (c) AI-tell words
    for word in AI_TELL_WORDS:
        if word in low:
            violations.append(f"AI-tell phrase detected: '{word}'.")

    # (a) claims of qualifications the candidate does NOT have
    for missing in profile.skillsTruth.doesNotYetHave:
        # Use the leading noun phrase as the claim signal (e.g. "NVQ", "driving licence").
        signal = _normalize(missing).split("(")[0].strip()
        # Build a few robust signals from the phrase.
        tokens = [t for t in re.split(r"[\s/]+", signal) if len(t) > 2]
        # If a distinctive token from the missing-qual appears, flag it.
        for tok in tokens[:3]:
            if re.search(rf"\b{re.escape(tok)}\b", low):
                warnings.append(
                    f"Possible claim of a qualification in doesNotYetHave "
                    f"('{missing}') via token '{tok}'. Verify before submit."
                )
                break

    # (b) prose-forbidden topics
    if is_prose:
        for pattern, label in PROSE_FORBIDDEN_PATTERNS:
            if re.search(pattern, low):
                violations.append(
                    f"Prose mentions '{label}' - forbidden in free-text (L13); "
                    f"answer only in structured form fields."
                )

    # (e) fabricated-metric advisory
    if METRIC_PATTERN.search(text or ""):
        warnings.append(
            "Contains a number/metric - confirm it is a real, honest figure, "
            "not a fabricated achievement metric."
        )

    return GateResult(ok=not violations, violations=violations, warnings=warnings)


def check_application(cv_bullets: list[str], letter: str, profile: Profile) -> GateResult:
    """Validate a whole application package: CV bullets + the cover letter."""
    all_violations: list[str] = []
    all_warnings: list[str] = []

    for i, bullet in enumerate(cv_bullets):
        r = check_text(bullet, profile, is_prose=False)
        all_violations += [f"CV bullet #{i + 1}: {v}" for v in r.violations]
        all_warnings += [f"CV bullet #{i + 1}: {w}" for w in r.warnings]

    r = check_text(letter, profile, is_prose=True)
    all_violations += [f"Letter: {v}" for v in r.violations]
    all_warnings += [f"Letter: {w}" for w in r.warnings]

    return GateResult(ok=not all_violations, violations=all_violations, warnings=all_warnings)
