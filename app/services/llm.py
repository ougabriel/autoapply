"""LLM service - the "model" layer, with keyless options.

You do NOT need an API key. Providers:
  - local   : a model running on your machine via Ollama (http://localhost:11434).
              No key, no cost, works for unattended background runs.
  - agent    : Kiro / a Claude agent IS the model. During an agent session the
              agent generates the letter and posts it to the app; the app reads it
              from a per-job inbox. No key. Only produces text while an agent is
              actively driving (otherwise the app falls back to the template).
  - anthropic / openai : hosted, key-based (still supported if you ever want them).

Hard rule, all providers: output ALWAYS passes the integrity gate before use. A
failed generation is retried with the violations fed back, then falls back to the
deterministic template. The model is never trusted to self-police honesty.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..models import Profile
from . import secrets

# Default provider: keyless local model. Override via secrets 'llm_provider'.
DEFAULT_PROVIDER = "local"
DEFAULT_LOCAL_MODEL = "llama3.1"
OLLAMA_URL = "http://localhost:11434/api/generate"

# Agent inbox: job-key -> generated letter text. Filled by the agent via the API,
# drained when tailoring asks for that job. Keeps the keyless agent path stateless.
_agent_inbox: dict[str, str] = {}


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    used_llm: bool


def provider() -> str:
    return secrets.get("llm_provider") or DEFAULT_PROVIDER


def is_active() -> bool:
    """True if the configured provider can currently produce text."""
    p = provider()
    if p == "local":
        return _ollama_available()
    if p == "agent":
        return True  # the agent path is always 'available'; it falls back if no answer
    if p == "anthropic":
        return bool(secrets.get("anthropic_api_key", "ANTHROPIC_API_KEY"))
    if p == "openai":
        return bool(secrets.get("openai_api_key", "OPENAI_API_KEY"))
    return False


def active_model() -> str:
    p = provider()
    if p == "local":
        return secrets.get("llm_model") or DEFAULT_LOCAL_MODEL
    if p == "agent":
        return "kiro-agent"
    return secrets.get("llm_model") or secrets._default_model(p)


# --------------------------------------------------------------------------- #
# Prompt construction (shared by all providers)
# --------------------------------------------------------------------------- #
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


def job_key(candidate: str, company: str, title: str) -> str:
    raw = f"{candidate}|{company}|{title}".lower()
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# Provider calls
# --------------------------------------------------------------------------- #
def _ollama_available() -> bool:
    import httpx

    try:
        with httpx.Client(timeout=1.5) as client:
            r = client.get("http://localhost:11434/api/tags")
            return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def _call_local(model: str, system: str, user: str) -> str:
    import httpx

    with httpx.Client(timeout=120.0) as client:
        r = client.post(OLLAMA_URL, json={
            "model": model,
            "prompt": user,
            "system": system,
            "stream": False,
        })
        r.raise_for_status()
        return (r.json().get("response") or "").strip()


def _call_agent(profile: Profile, company: str, title: str) -> str | None:
    """Drain a letter the agent posted for this job. None if none waiting."""
    return _agent_inbox.pop(job_key(profile.candidate, company, title), None)


def provide_agent_letter(candidate: str, company: str, title: str, letter: str) -> str:
    """Agent posts a generated letter for a job; tailoring will pick it up."""
    key = job_key(candidate, company, title)
    _agent_inbox[key] = letter
    return key


def agent_prompt(profile: Profile, company: str, title: str, description: str,
                 matched_strengths: list[str]) -> dict:
    """The exact prompt an agent should answer to act as the model (keyless)."""
    return {
        "job_key": job_key(profile.candidate, company, title),
        "system": _system_prompt(profile),
        "user": _user_prompt(profile, company, title, description, matched_strengths),
    }


def _call_anthropic(model: str, key: str, system: str, user: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=key)
    resp = client.messages.create(
        model=model, max_tokens=600, system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()


def _call_openai(model: str, key: str, system: str, user: str) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=key)
    resp = client.chat.completions.create(
        model=model, max_tokens=600,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    return (resp.choices[0].message.content or "").strip()


# --------------------------------------------------------------------------- #
# Public generation
# --------------------------------------------------------------------------- #
def generate_letter(profile: Profile, company: str, title: str, description: str,
                    matched_strengths: list[str], *, max_retries: int = 2):
    """Generate a letter with the active provider. Returns LLMResult | None.

    None means "no model produced text - caller uses the template". The agent
    provider returns None when no agent answer is waiting (so the loop never
    blocks on an agent that isn't there).
    """
    p = provider()
    model = active_model()
    system = _system_prompt(profile)

    from . import integrity_gate  # local import avoids a cycle

    # Agent path: single-shot - the agent already wrote it; just gate it.
    if p == "agent":
        text = _call_agent(profile, company, title)
        if not text:
            return None
        gate = integrity_gate.check_text(text, profile, is_prose=True)
        if gate.ok:
            return LLMResult(text=text, provider=p, model=model, used_llm=True)
        return LLMResult(text="__GATE_FAILED__", provider=p, model=model, used_llm=False)

    # Keyless local or keyed hosted: generate with gate-feedback retries.
    if p in ("anthropic", "openai"):
        key = secrets.llm_api_key()
        if not key:
            return None

    feedback = ""
    for _ in range(max_retries + 1):
        user = _user_prompt(profile, company, title, description, matched_strengths, feedback)
        try:
            if p == "local":
                text = _call_local(model, system, user)
            elif p == "openai":
                text = _call_openai(model, secrets.llm_api_key(), system, user)
            elif p == "anthropic":
                text = _call_anthropic(model, secrets.llm_api_key(), system, user)
            else:
                return None
        except Exception as exc:  # noqa: BLE001 - any failure -> template fallback
            return LLMResult(text=f"__LLM_ERROR__:{exc}", provider=p, model=model, used_llm=False)

        gate = integrity_gate.check_text(text, profile, is_prose=True)
        if gate.ok:
            return LLMResult(text=text, provider=p, model=model, used_llm=True)
        feedback = "; ".join(gate.violations)

    return LLMResult(text="__GATE_FAILED__", provider=p, model=model, used_llm=False)
