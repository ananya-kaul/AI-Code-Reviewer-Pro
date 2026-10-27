"""Core review engine using OpenAI-compatible provider APIs.

Uses the OpenAI-compatible Chat Completions interface, so the same code drives
several providers just by pointing at a different ``base_url``:

    * Groq        — free, fast, no credit card   (GROQ_API_KEY)
    * Google Gemini                              (GEMINI_API_KEY)
    * OpenRouter  — free ``:free`` models         (OPENROUTER_API_KEY)
    * OpenAI      — paid                          (OPENAI_API_KEY)

The provider is auto-detected from whichever API key is present (Groq first),
or forced with the ``LLM_PROVIDER`` environment variable.
"""

from __future__ import annotations

import os
import ast
import re
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
        "openai/gpt-oss-120b",
        "https://console.groq.com/keys",
    ),
    "gemini": Provider(
        "gemini",
        "Google Gemini",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "GEMINI_API_KEY",
        "gemini-3.6-flash",
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

# Retry only when a model ID has disappeared or this key cannot access it.
# Never retry authentication, quota, or provider outages against another model.
_MODEL_FALLBACKS = {
    "groq": ("openai/gpt-oss-120b", "openai/gpt-oss-20b"),
    "gemini": ("gemini-3.6-flash", "gemini-3.5-flash-lite"),
}


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


def _chat(client, model: str, system: str, user: str, json_mode: bool,
          provider_key: str = ""):
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
    if provider_key == "groq" and model.startswith("openai/gpt-oss-"):
        kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
        kwargs["reasoning_format"] = "hidden"
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

    # The app remains useful on first launch, before a provider key is added.
    # These are deliberately narrow static checks, clearly labeled in the UI.
    if not os.getenv("LLM_PROVIDER") and not any(
        os.getenv(PROVIDERS[name].env_var, "").strip() for name in _DETECT_ORDER
    ):
        return local_review(code, language)

    provider, api_key = resolve_provider()
    client = _make_client(provider, api_key)
    model = model or _resolve_model(provider)

    system_prompt = build_system_prompt(language)
    user_prompt = build_user_prompt(code, language, mode)

    # Prefer strict JSON mode; some models/providers don't support it, so retry
    # once in plain mode and rely on defensive JSON extraction.
    response = None
    last_error: Exception | None = None
    candidates = [model, *(
        candidate for candidate in _MODEL_FALLBACKS.get(provider.key, ())
        if candidate != model
    )]
    for candidate in candidates:
        for json_mode in (True, False):
            try:
                response = _chat(
                    client, candidate, system_prompt, user_prompt, json_mode,
                    provider.key,
                )
                model = candidate
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if json_mode and _json_mode_unsupported(exc):
                    continue
                break
        if response is not None or not _model_unavailable(last_error):
            break

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


def local_review(code: str, language: str) -> ReviewResult:
    """Run a small, explainable set of checks without executing user code."""
    metrics = local_metrics(code)
    bugs: list[dict[str, str]] = []
    security: list[dict[str, str]] = []
    performance: list[dict[str, str]] = []
    practices: list[dict[str, str]] = []

    def finding(target, severity, line, title, detail, fix):
        target.append({"severity": severity, "impact": severity, "line": str(line),
                       "title": title, "detail": detail, "fix": fix})

    if language == "Python":
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            finding(bugs, "high", exc.lineno or 1, "Python syntax error",
                    exc.msg, "Correct the syntax before running this code.")
        else:
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for default in node.args.defaults + [d for d in node.args.kw_defaults if d]:
                        if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                            finding(bugs, "medium", node.lineno, "Mutable default argument",
                                    f"{node.name} shares this default object across calls.",
                                    "Use None as the default and create the collection inside the function.")
                    if any(isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                           and child.func.id == node.name for child in ast.walk(node)):
                        finding(performance, "medium", node.lineno, "Recursive call",
                                f"{node.name} calls itself; review the depth and repeated work.",
                                "Check the base case and consider caching or an iterative approach.")
                elif isinstance(node, ast.ExceptHandler) and node.type is None:
                    finding(practices, "medium", node.lineno, "Bare except clause",
                            "This also catches KeyboardInterrupt and SystemExit.",
                            "Catch the specific exception types you expect.")
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                        and node.func.id in {"eval", "exec"}:
                    finding(security, "high", node.lineno, "Dynamic code execution",
                            f"{node.func.id} can execute untrusted input.",
                            "Use a parser or a restricted data format instead.")
    else:
        for number, line in enumerate(code.splitlines(), 1):
            if language in {"JavaScript", "TypeScript"} and re.search(r"\beval\s*\(", line):
                finding(security, "high", number, "Dynamic code execution",
                        "eval can execute untrusted input.", "Use a parser instead of eval.")
            if language == "SQL" and re.search(r"\bSELECT\s+\*\s+FROM\b", line, re.I):
                finding(performance, "low", number, "SELECT * query",
                        "Fetching every column may transfer unnecessary data.",
                        "Select only the columns the caller needs.")

    count = len(bugs) + len(security) + len(performance) + len(practices)
    summary = (
        f"Local static checks found {count} potential issue{'s' if count != 1 else ''}. "
        "This is a limited scan; configure an AI provider for a full review, "
        "suggested rewrite, and broader reasoning. No code was sent to a provider."
    )
    proxy = metrics["cyclomatic_proxy"]
    rating = "high" if proxy > 15 else "moderate" if proxy > 7 else "low"
    data = {
        "overall_score": "—", "summary": summary, "bugs": bugs,
        "security_issues": security, "performance_issues": performance,
        "architecture_review": {"assessment": "Architecture requires a full AI review.",
                                "strengths": [], "concerns": [], "suggestions": []},
        "best_practices": practices,
        "complexity": {"rating": rating, "estimated_time_complexity": "Not estimated",
                       "estimated_space_complexity": "Not estimated",
                       "cyclomatic_estimate": proxy,
                       "notes": "Branch count is a static proxy, not a full complexity analysis."},
        "refactored_code": "",
    }
    return ReviewResult(data=data, metrics=metrics, provider="Local static checks")


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


def _model_unavailable(exc: Exception | None) -> bool:
    if exc is None or getattr(exc, "status_code", None) not in (400, 404):
        return False
    message = str(exc).lower()
    return "model" in message and any(term in message for term in (
        "does not exist", "do not have access", "not found",
        "decommissioned", "not available", "not supported",
    ))


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
