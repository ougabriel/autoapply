"""LLM service - the "model activation" layer.

This is where the AI plugs in. When an API key is configured, the tailoring step
asks the model to write the cover letter in the candidate's honest voice; when no
key is set, the app falls back to the deterministic template so it always works.

Hard rule: whatever produces the text, the output is ALWAYS run through the
integrity gate before it can be used. The model is never trusted to self-police
the honesty contract - the gate does, and a failed generation is retried with the
violations fed back, then falls back to the template if still failing.

Providers: Anthropic (default) and OpenAI. Both imported lazily so the app runs
without either SDK installed until you activate one.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..models import Profile
from . import secrets


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    used_llm: bool


def is_active() -> bool:
    """True if an LLM is configured and usable."""
    return bool(secrets.llm_api_key())


def _system_prompt(profile: Profile) -> str:
    has = "; ".join(profile.skillsTruth.has)
    not_have = "; ".join(profile.skillsTruth.doesNotYetHave)
    return (
        "You write UK job-application cover letters in the candidate's own honest, "
        "warm, plain voice. Strict rules, no exceptions:\n"
        "- Write about FIT and genuine interest only.\n"
        "- NEVER mention visa, sponsorship, certificate of sponsorship, salary, or "
        "notice period. Those belong only in structured form fields.\n"
        "- NEVER use em-dashes. NEVER use AI-tell words (leverage, delve, robust, "
        "seamless, passionate about leveraging, etc.). Use contractions, be specific.\n"
        "- Claim ONLY these real strengths: " + has + ".\n"
        "- NEVER claim any of these (the candidate does not have them): " + not_have + ".\n"
        "- Do not invent employers, dates, achievements, or metrics.\n"
        "Return only the letter body, no preamble."
    )


def _user_prompt(profile: Profile, company: str, title: str, description: str,
                 matched_strengths: list[str], feedback: str = "") -> str:
    base = (
        f"Candidate: {profile.candidate}\n"
        f"Role: {title} at {company}\n"
        f"Most relevant real strengths: {', '.join(matched_strengths)}\n\n"
        f"Job description / person spec:\n{description[:2000]}\n\n"
        "Write a short cover letter (about 150 words)."
    )
    if feedback:
        base += ("\n\nYour previous attempt was rejected for these reasons - fix them: "
                 + feedback)
    return base


def _call_anthropic(model: str, key: str, system: str, user: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=key)
    resp = client.messages.create(
        model=model, max_tokens=600, system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(block.text for block in resp.content if getattr(block, "type", "") == "text").strip()


def _call_openai(model: str, key: str, system: str, user: str) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=key)
    resp = client.chat.completions.create(
        model=model, max_tokens=600,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    return (resp.choices[0].message.content or "").strip()


def generate_letter(profile: Profile, company: str, title: str, description: str,
                    matched_strengths: list[str], *, max_retries: int = 2):
    """Generate a letter with the active LLM. Returns (LLMResult | None).

    Returns None if no LLM is active (caller falls back to the template). Imports
    of the SDKs are lazy so a missing package degrades gracefully to None.
    """
    key = secrets.llm_api_key()
    if not key:
        return None

    provider = secrets.get("llm_provider") or "anthropic"
    model = secrets.get("llm_model") or secrets._default_model(provider)
    system = _system_prompt(profile)

    # Local import to avoid a cycle (integrity_gate imports nothing from here).
    from . import integrity_gate

    feedback = ""
    for _ in range(max_retries + 1):
        user = _user_prompt(profile, company, title, description, matched_strengths, feedback)
        try:
            if provider == "openai":
                text = _call_openai(model, key, system, user)
            else:
                text = _call_anthropic(model, key, system, user)
        except Exception as exc:  # noqa: BLE001 - any SDK/network failure -> template fallback
            return LLMResult(text=f"__LLM_ERROR__:{exc}", provider=provider, model=model, used_llm=False)

        gate = integrity_gate.check_text(text, profile, is_prose=True)
        if gate.ok:
            return LLMResult(text=text, provider=provider, model=model, used_llm=True)
        feedback = "; ".join(gate.violations)

    # Still failing after retries: signal fallback to the template.
    return LLMResult(text="__GATE_FAILED__", provider=provider, model=model, used_llm=False)
