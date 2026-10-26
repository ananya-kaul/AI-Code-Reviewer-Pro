"""Core review engine — provider-agnostic, works with free LLM APIs.

Uses the OpenAI-compatible Chat Completions interface, so the same code drives
several providers just by pointing at a different ``base_url``:

    * Groq        — free, fast, no credit card   (GROQ_API_KEY)
    * Google Gemini — free tier                  (GEMINI_API_KEY)
    * OpenRouter  — free ``:free`` models         (OPENROUTER_API_KEY)
    * OpenAI      — paid                          (OPENAI_API_KEY)

The provider is auto-detected from whichever API key is present (Groq first),
or forced with the ``LLM_PROVIDER`` environment variable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from prompts import build_system_prompt, build_user_prompt
from utils import extract_json, local_metrics


@dataclass(frozen=True)
class Provider:
    """Static configuration for an OpenAI-compatible LLM provider."""

    key: str          # internal id
    label: str        # human name
    base_url: str | None
    env_var: str      # environment variable holding the API key
    default_model: str
    signup_url: str


# Ordered by preference for auto-detection (free options first).
PROVIDERS: dict[str, Provider] = {
    "groq": Provider(
        "groq",
        "Groq",
        "https://api.groq.com/openai/v1",
        "GROQ_API_KEY",
        "llama-3.3-70b-versatile",
        "https://console.groq.com/keys",
    ),
    "gemini": Provider(
        "gemini",
        "Google Gemini",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "GEMINI_API_KEY",
        "gemini-2.0-flash",
        "https://aistudio.google.com/app/apikey",
    ),
    "openrouter": Provider(
        "openrouter",
        "OpenRouter",
        "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY",
        "meta-llama/llama-3.3-70b-instruct:free",
        "https://openrouter.ai/keys",
    ),
    "openai": Provider(
        "openai",
        "OpenAI",
        None,  # SDK default endpoint
        "OPENAI_API_KEY",
        "gpt-4o-mini",
        "https://platform.openai.com/api-keys",
    ),
}

_DETECT_ORDER = ["groq", "gemini", "openrouter", "openai"]


class ReviewError(RuntimeError):
    """Raised when a review cannot be produced (config, API, or parse error)."""


@dataclass
class ReviewResult:
    """Structured outcome of a review, plus locally computed metrics."""

    data: dict[str, Any]
    metrics: dict[str, int] = field(default_factory=dict)
    provider: str = ""
    model: str = ""


def resolve_provider() -> tuple[Provider, str]:
    """Pick a provider + API key from the environment.

    Honors an explicit ``LLM_PROVIDER`` if its key is set; otherwise returns the
    first provider (in preference order) that has an API key configured.
    """
    forced = os.getenv("LLM_PROVIDER", "").strip().lower()
    if forced:
        provider = PROVIDERS.get(forced)
        if not provider:
            raise ReviewError(
                f"Unknown LLM_PROVIDER '{forced}'. Choose one of: "
                f"{', '.join(PROVIDERS)}."
            )
        api_key = os.getenv(provider.env_var, "").strip()
        if not api_key:
            raise ReviewError(
                f"{provider.label} selected but {provider.env_var} is not set. "
                f"Get a free key at {provider.signup_url}."
            )
        return provider, api_key

    for name in _DETECT_ORDER:
        provider = PROVIDERS[name]
        api_key = os.getenv(provider.env_var, "").strip()
        if api_key:
            return provider, api_key

    raise ReviewError(
        "No API key found. Set one of these (free options first):\n"
        "• GROQ_API_KEY — free, no card — https://console.groq.com/keys\n"
        "• GEMINI_API_KEY — free — https://aistudio.google.com/app/apikey\n"
        "• OPENROUTER_API_KEY — free models — https://openrouter.ai/keys\n"
        "• OPENAI_API_KEY — paid — https://platform.openai.com/api-keys"
    )


def _resolve_model(provider: Provider) -> str:
    """Model override precedence: LLM_MODEL, legacy OPENAI_MODEL, provider default."""
    return (
        os.getenv("LLM_MODEL")
        or (os.getenv("OPENAI_MODEL") if provider.key == "openai" else None)
        or provider.default_model
    )


def _make_client(provider: Provider, api_key: str):
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise ReviewError(
            "The 'openai' package is not installed. Run: pip install -r "
            "requirements.txt"
        ) from exc
    kwargs: dict[str, Any] = {"api_key": api_key}
    if provider.base_url:
        kwargs["base_url"] = provider.base_url
    return OpenAI(**kwargs)


def _chat(client, model: str, system: str, user: str, json_mode: bool):
    """One Chat Completions call; json_mode requests a strict JSON object."""
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "max_tokens": 4096,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    return client.chat.completions.create(**kwargs)


def review_code(
    code: str,
    language: str,
    mode: str,
    model: str | None = None,
) -> ReviewResult:
    """Run a single code review and return a structured result."""
    if not code or not code.strip():
        raise ReviewError("Please paste some code to review.")

    provider, api_key = resolve_provider()
    client = _make_client(provider, api_key)
    model = model or _resolve_model(provider)

    system_prompt = build_system_prompt(language)
    user_prompt = build_user_prompt(code, language, mode)

    # Prefer strict JSON mode; some models/providers don't support it, so retry
    # once in plain mode and rely on defensive JSON extraction.
    response = None
    last_error: Exception | None = None
    for json_mode in (True, False):
        try:
            response = _chat(client, model, system_prompt, user_prompt, json_mode)
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            # A failed request should only be retried without JSON mode when
            # that feature appears to be the unsupported part of the request.
            if not json_mode or not _json_mode_unsupported(exc):
                break
            continue

    if response is None:
        raise ReviewError(
            f"{provider.label} request failed: {last_error}"
        ) from last_error

    text = _response_text(response)
    if not text:
        raise ReviewError(f"{provider.label} returned an empty response.")

    try:
        data = extract_json(text)
    except ValueError as exc:
        raise ReviewError(
            f"Could not parse the model's response as JSON. {exc}"
        ) from exc

    return ReviewResult(
        data=_normalize(data),
        metrics=local_metrics(code),
        provider=provider.label,
        model=model,
    )


def _response_text(response: Any) -> str:
    """Extract assistant text from a Chat Completions result."""
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError):
        return ""
    if isinstance(content, str):
        return content.strip()
    # Some providers return content as a list of parts.
    if isinstance(content, list):
        parts = [p.get("text", "") if isinstance(p, dict) else str(p) for p in content]
        return "".join(parts).strip()
    return ""


def _json_mode_unsupported(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(term in message for term in ("response_format", "json_object", "json mode"))


# Keys that must always exist so the renderer never trips on missing fields.
_ARRAY_KEYS = ("bugs", "security_issues", "performance_issues", "best_practices")
_OBJECT_KEYS = ("architecture_review", "complexity")


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    """Ensure every expected key exists with a sane default."""
    if not isinstance(data, dict):
        raise ReviewError("The model returned a JSON value instead of a review object.")
    for key in _ARRAY_KEYS:
        if not isinstance(data.get(key), list):
            data[key] = []
        else:
            data[key] = [item for item in data[key] if isinstance(item, dict)]
    for key in _OBJECT_KEYS:
        if not isinstance(data.get(key), dict):
            data[key] = {}
    architecture = data["architecture_review"]
    architecture["assessment"] = str(architecture.get("assessment") or "")
    for key in ("strengths", "concerns", "suggestions"):
        value = architecture.get(key)
        architecture[key] = [str(item) for item in value] if isinstance(value, list) else []
    for key in ("summary", "refactored_code"):
        data[key] = str(data.get(key) or "")
    try:
        score = float(data.get("overall_score"))
        data["overall_score"] = round(max(0.0, min(10.0, score)), 1)
    except (TypeError, ValueError):
        data["overall_score"] = "—"
    return data
