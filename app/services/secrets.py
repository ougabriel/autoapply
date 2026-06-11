"""Local secrets store - API key + reusable ATS password (gitignored file).

Secrets live in secrets.local.json at the project root, never committed. The
store also reads environment variables as a fallback so you can run with
ANTHROPIC_API_KEY / OPENAI_API_KEY set instead of writing them to disk.

Values are never echoed back in full - the API returns only whether a secret is
present and a masked preview.
"""
from __future__ import annotations

import json
import os
from typing import Any

from .. import config


def _read() -> dict[str, Any]:
    if not config.SECRETS_FILE.exists():
        return {}
    try:
        return json.loads(config.SECRETS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write(data: dict[str, Any]) -> None:
    config.SECRETS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get(key: str, env_fallback: str | None = None) -> str | None:
    val = _read().get(key)
    if val:
        return val
    if env_fallback:
        return os.environ.get(env_fallback)
    return None


def set_value(key: str, value: str) -> None:
    data = _read()
    data[key] = value
    _write(data)


def llm_api_key() -> str | None:
    """The active LLM API key, by provider preference."""
    provider = get("llm_provider") or "anthropic"
    if provider == "anthropic":
        return get("anthropic_api_key", env_fallback="ANTHROPIC_API_KEY")
    if provider == "openai":
        return get("openai_api_key", env_fallback="OPENAI_API_KEY")
    return None


def _mask(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}...{value[-4:]}"


def status() -> dict:
    """Non-sensitive view for the UI: what's set, masked previews only."""
    provider = get("llm_provider") or "anthropic"
    return {
        "llm_provider": provider,
        "llm_model": get("llm_model") or _default_model(provider),
        "anthropic_api_key_set": bool(get("anthropic_api_key", "ANTHROPIC_API_KEY")),
        "openai_api_key_set": bool(get("openai_api_key", "OPENAI_API_KEY")),
        "active_key_preview": _mask(llm_api_key()),
        "ats_password_set": bool(get("ats_reusable_password")),
    }


def _default_model(provider: str) -> str:
    return "claude-3-5-sonnet-20241022" if provider == "anthropic" else "gpt-4o"
