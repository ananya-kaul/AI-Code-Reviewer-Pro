"""Utility helpers: catalogs, local complexity heuristics, and report rendering."""

from __future__ import annotations

import datetime as _dt
import json
import re
from typing import Any

# ---------------------------------------------------------------------------
# Catalogs used by the UI
# ---------------------------------------------------------------------------

LANGUAGES: list[str] = [
    "Swift",
    "Dart",
    "Python",
    "JavaScript",
    "TypeScript",
    "Java",
    "Kotlin",
    "Go",
    "Rust",
    "C#",
    "C++",
    "PHP",
    "Ruby",
    "SQL",
    "HTML/CSS",
]

# Maps a display language to the token used by the Gradio Code component so the
# editor highlights syntax correctly.
GRADIO_CODE_LANGUAGE: dict[str, str | None] = {
    "Swift": "python",  # closest available highlighter for Swift-like syntax
    "Dart": "typescript",
    "Python": "python",
    "JavaScript": "javascript",
    "TypeScript": "typescript",
    "Java": "java",
    "Kotlin": "java",
    "Go": "go",
    "Rust": "rust",
    "C#": "csharp",
    "C++": "cpp",
    "PHP": "php",
    "Ruby": "python",
    "SQL": "sql",
    "HTML/CSS": "html",
}

REVIEW_MODE_NAMES: list[str] = [
    "General",
    "Bugs",
    "Performance",
    "Security",
    "Best Practices",
    "Clean Architecture",
    "Refactoring",
    "Testing",
]

SEVERITY_BADGE: dict[str, str] = {
    "critical": "🔴 Critical",
    "high": "🟠 High",
    "medium": "🟡 Medium",
    "low": "🟢 Low",
    "very high": "🔴 Very High",
    "moderate": "🟡 Moderate",
}


def badge(level: str) -> str:
    """Return a human-friendly severity/impact badge."""
    return SEVERITY_BADGE.get((level or "").strip().lower(), f"⚪ {level or 'n/a'}")


def code_language_token(language: str) -> str | None:
    """Highlighter token for the Gradio Code editor."""
    return GRADIO_CODE_LANGUAGE.get(language, "python")


# ---------------------------------------------------------------------------
# Heuristic language auto-detection
# ---------------------------------------------------------------------------

# Each rule is (language, weight, compiled-pattern). Distinctive markers get a
# higher weight so overlapping languages (JS/TS, Java/Kotlin/C#) disambiguate.
def _r(pattern: str) -> "re.Pattern[str]":
    return re.compile(pattern, re.MULTILINE | re.IGNORECASE)


_DETECTION_RULES: list[tuple[str, int, "re.Pattern[str]"]] = [
    # Swift
    ("Swift", 5, _r(r"\bimport\s+(Foundation|SwiftUI|UIKit)\b")),
    ("Swift", 4, _r(r"\bfunc\s+\w+\s*\([^)]*\)\s*(->|\{)")),
    ("Swift", 3, _r(r"\bguard\s+let\b|\bif\s+let\b")),
    ("Swift", 2, _r(r"\b(let|var)\s+\w+\s*:\s*\w+")),
    # Dart / Flutter
    ("Dart", 5, _r(r"import\s+'package:")),
    ("Dart", 5, _r(r"\bWidget\s+build\s*\(|extends\s+(StatelessWidget|StatefulWidget)")),
    ("Dart", 3, _r(r"\bvoid\s+main\s*\(\s*\)\s*\{")),
    # Python
    ("Python", 5, _r(r"^\s*def\s+\w+\s*\(.*\)\s*:")),
    ("Python", 4, _r(r"^\s*(from\s+\w+\s+import|import\s+\w+)")),
    ("Python", 3, _r(r"\bself\b|\belif\b|\b(None|True|False)\b")),
    ("Python", 3, _r(r'\bprint\s*\(|__name__\s*==\s*["\']__main__')),
    # TypeScript (check before JS; needs type annotations)
    ("TypeScript", 6, _r(r"\binterface\s+\w+\s*\{|\btype\s+\w+\s*=")),
    ("TypeScript", 5, _r(r":\s*(string|number|boolean|void|any|unknown)\b")),
    ("TypeScript", 4, _r(r"\b(enum|implements)\b|\bas\s+\w+")),
    # JavaScript
    ("JavaScript", 4, _r(r"\bconst\s+\w+\s*=|\blet\s+\w+\s*=|\bfunction\s*\*?\s*\w*\s*\(")),
    ("JavaScript", 4, _r(r"\bconsole\.(log|error|warn)\b|\brequire\s*\(|\bmodule\.exports\b")),
    ("JavaScript", 3, _r(r"=>|\bdocument\.|\bwindow\.")),
    # Java
    ("Java", 6, _r(r"\bpublic\s+(static\s+)?(class|void\s+main)\b")),
    ("Java", 5, _r(r"\bSystem\.out\.print|import\s+java\.")),
    ("Java", 3, _r(r"\b(public|private|protected)\s+\w+\s+\w+\s*\(")),
    # Kotlin
    ("Kotlin", 6, _r(r"\bfun\s+\w+\s*\(")),
    ("Kotlin", 5, _r(r"\bimport\s+kotlin|companion\s+object|\bprintln\s*\(")),
    ("Kotlin", 3, _r(r"\b(val|var)\s+\w+\s*(:\s*\w+)?\s*=")),
    # Go
    ("Go", 7, _r(r"\bpackage\s+main\b|\bfunc\s+main\s*\(\s*\)")),
    ("Go", 5, _r(r"\bfmt\.(Print|Sprint)|import\s*\(|:=")),
    ("Go", 3, _r(r"\bfunc\s+\(\w+\s+\*?\w+\)")),
    # Rust
    ("Rust", 7, _r(r"\bfn\s+\w+\s*\(|\blet\s+mut\b")),
    ("Rust", 6, _r(r"println!|use\s+std::|\bimpl\b|\bmatch\b\s+\w+\s*\{")),
    # C#
    ("C#", 7, _r(r"\busing\s+System\b|\bnamespace\s+\w+|Console\.Write")),
    ("C#", 4, _r(r"\bpublic\s+class\b|\bstatic\s+void\s+Main\b")),
    # C++
    ("C++", 7, _r(r"#include\s*<\w+>")),
    ("C++", 6, _r(r"\bstd::|\bcout\b|\busing\s+namespace\s+std\b")),
    ("C++", 3, _r(r"\bint\s+main\s*\(")),
    # PHP
    ("PHP", 8, _r(r"<\?php")),
    ("PHP", 4, _r(r"\$\w+\s*=|\becho\s+|->\w+\s*\(")),
    # Ruby
    ("Ruby", 6, _r(r"\bdef\s+\w+.*\n(.*\n)*?\s*end\b|\bputs\s+")),
    ("Ruby", 4, _r(r"\brequire\s+['\"]|\.each\s+do\b|\battr_accessor\b")),
    # SQL
    ("SQL", 7, _r(r"\bSELECT\b[\s\S]*\bFROM\b")),
    ("SQL", 6, _r(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|CREATE\s+TABLE|ALTER\s+TABLE)\b")),
    # HTML / CSS
    ("HTML/CSS", 8, _r(r"<!DOCTYPE\s+html|<html\b|<div\b|<body\b")),
    ("HTML/CSS", 5, _r(r"</\w+>|<\w+[^>]*>")),
    ("HTML/CSS", 4, _r(r"[.#]?[\w-]+\s*\{[^}]*:[^}]*;")),
]


def detect_language(code: str) -> tuple[str, float]:
    """Guess the programming language of a snippet.

    Returns ``(language, confidence)`` where confidence is a 0-1 score. Falls
    back to ``("Python", 0.0)`` on empty or unrecognizable input.
    """
    if not code or not code.strip():
        return "Python", 0.0

    scores: dict[str, int] = {}
    for language, weight, pattern in _DETECTION_RULES:
        if pattern.search(code):
            scores[language] = scores.get(language, 0) + weight

    if not scores:
        return "Python", 0.0

    best = max(scores, key=scores.get)
    total = sum(scores.values()) or 1
    confidence = round(scores[best] / total, 2)
    return best, confidence


# ---------------------------------------------------------------------------
# Local, dependency-free complexity metrics (a quick heuristic layer that
# complements the model's qualitative complexity analysis).
# ---------------------------------------------------------------------------

_BRANCH_KEYWORDS = re.compile(
    r"\b(if|elif|else if|for|while|case|catch|except|&&|\|\||\?)\b"
)


def local_metrics(code: str) -> dict[str, int]:
    """Compute lightweight static metrics without executing the code."""
    lines = code.splitlines()
    non_blank = [ln for ln in lines if ln.strip()]
    comment_prefixes = ("#", "//", "/*", "*", "--", "<!--")
    comments = [ln for ln in non_blank if ln.strip().startswith(comment_prefixes)]
    branches = len(_BRANCH_KEYWORDS.findall(code))
    return {
        "total_lines": len(lines),
        "code_lines": len(non_blank) - len(comments),
        "comment_lines": len(comments),
        "branch_points": branches,
        # A rough cyclomatic proxy: one base path plus each branch point.
        "cyclomatic_proxy": branches + 1,
    }


# ---------------------------------------------------------------------------
# JSON extraction (defensive against stray prose or code fences)
# ---------------------------------------------------------------------------

def extract_json(text: str) -> dict[str, Any]:
    """Best-effort extraction of the JSON object from a model response."""
    if not text:
        raise ValueError("Empty response from the model.")

    cleaned = text.strip()
    # Strip markdown fences if the model added them despite instructions.
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Fall back to the outermost { ... } span.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(cleaned[start : end + 1])

    raise ValueError("Could not parse a JSON object from the model response.")


# ---------------------------------------------------------------------------
# Markdown report rendering
# ---------------------------------------------------------------------------

def _render_findings(items: list[dict[str, Any]], key: str) -> str:
    if not items:
        return "_No issues detected by this review._\n"
    out = []
    for i, it in enumerate(items, 1):
        level = it.get("severity") or it.get("impact") or "n/a"
        line = it.get("line", "n/a")
        title = it.get("title", "Untitled finding")
        detail = it.get("detail", "")
        fix = it.get("fix", "")
        out.append(f"**{i}. {title}** — {badge(level)} · _line {line}_\n\n{detail}")
        if fix:
            out.append(f"\n> **Fix:** {fix}")
        out.append("\n")
    return "\n".join(out)


def render_markdown_report(
    result: dict[str, Any],
    language: str,
    mode: str,
    metrics: dict[str, int],
    reviewer_label: str = "",
) -> str:
    """Render the full structured result into a shareable Markdown report."""
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    score = result.get("overall_score", "—")
    summary = result.get("summary", "")
    arch = result.get("architecture_review", {}) or {}
    complexity = result.get("complexity", {}) or {}

    md: list[str] = []
    md.append(f"# Code Review Report\n")
    md.append(
        f"**Language:** {language}  ·  **Mode:** {mode}  ·  "
        f"**Review:** {reviewer_label or 'AI provider'}  ·  "
        f"**Generated:** {stamp}\n"
    )
    md.append(f"## Overall Score: {score} / 10\n" if isinstance(score, (int, float))
              else "## Overall Score: Not available\n")
    if summary:
        md.append(f"{summary}\n")

    md.append("## 🐞 Bugs\n")
    md.append(_render_findings(result.get("bugs", []), "bugs"))

    md.append("## 🔒 Security Issues\n")
    md.append(_render_findings(result.get("security_issues", []), "security"))

    md.append("## ⚡ Performance Issues\n")
    md.append(_render_findings(result.get("performance_issues", []), "performance"))

    md.append("## 🏛️ Architecture Review\n")
    if arch.get("assessment"):
        md.append(arch["assessment"] + "\n")
    if arch.get("strengths"):
        md.append("**Strengths**\n")
        md.extend(f"- {s}" for s in arch["strengths"])
        md.append("")
    if arch.get("concerns"):
        md.append("**Concerns**\n")
        md.extend(f"- {c}" for c in arch["concerns"])
        md.append("")
    if arch.get("suggestions"):
        md.append("**Suggestions**\n")
        md.extend(f"- {s}" for s in arch["suggestions"])
        md.append("")

    md.append("## ✅ Best-Practice Suggestions\n")
    bp = result.get("best_practices", [])
    if bp:
        for i, item in enumerate(bp, 1):
            md.append(f"**{i}. {item.get('title', '')}**\n\n{item.get('detail', '')}\n")
    else:
        md.append("_No additional suggestions._\n")

    md.append("## 📊 Complexity Analysis\n")
    md.append(f"- **Rating:** {complexity.get('rating', '—')}")
    md.append(
        f"- **Time complexity:** {complexity.get('estimated_time_complexity', '—')}"
    )
    md.append(
        f"- **Space complexity:** {complexity.get('estimated_space_complexity', '—')}"
    )
    md.append(
        f"- **Cyclomatic estimate:** "
        f"{complexity.get('cyclomatic_estimate', '—')}"
    )
    md.append(
        f"- **Cyclomatic (static proxy):** {metrics.get('cyclomatic_proxy', '—')}"
    )
    md.append(
        f"- **Lines:** {metrics.get('total_lines', 0)} total · "
        f"{metrics.get('code_lines', 0)} code · "
        f"{metrics.get('comment_lines', 0)} comment"
    )
    if complexity.get("notes"):
        md.append(f"\n{complexity['notes']}\n")

    md.append("\n## ♻️ Refactored Code\n")
    refactored = result.get("refactored_code", "")
    if refactored:
        fence = code_language_token(language) or ""
        md.append(f"```{fence}\n{refactored}\n```")
    else:
        md.append("_No refactor produced._")

    return "\n".join(md).strip() + "\n"


def score_label(score: Any) -> str:
    """Format the score for the headline display component."""
    try:
        value = float(score)
    except (TypeError, ValueError):
        return "— / 10"
    return f"{value:.1f} / 10"
