"""AI Code Reviewer Pro — Gradio application entry point.

A light, responsive UI for reviewing source code across many
languages and review lenses. The heavy lifting lives in `reviewer.py`; this
module wires the interface, formats results, and handles errors gracefully.
"""

from __future__ import annotations

import datetime as _dt
import html
import textwrap
import os
from pathlib import Path
from uuid import uuid4

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv is optional; env vars can be set directly
    pass

import gradio as gr

from reviewer import ReviewError, review_code
from utils import (
    LANGUAGES,
    REVIEW_MODE_NAMES,
    badge,
    code_language_token,
    detect_language,
    render_markdown_report,
)

APP_DIR = Path(__file__).parent
THEME_CSS = (APP_DIR / "theme.css").read_text(encoding="utf-8")
REPORTS_DIR = APP_DIR / "assets" / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

PLACEHOLDER_CODE = """def fibonacci(n):
    # Naive recursive Fibonacci — try reviewing this!
    if n <= 1:
        return n
    return fibonacci(n - 1) + fibonacci(n - 2)

result = fibonacci(35)
print(result)
"""

# Injected once into <head>; fonts and light color scheme.
HEAD_HTML = (
    '<meta name="color-scheme" content="light">'
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?'
    "family=Inter:wght@400;500;600;700;800&"
    'family=JetBrains+Mono:wght@400;500;700&display=swap" '
    'rel="stylesheet">'
    '<style>@media(max-width:620px){.gradio-container{padding:0 16px 25px!important}'
    '.gradio-container main.fillable{padding:0!important}}</style>'
)


BRAND_HTML = """
<header class="app-header">
  <div class="brand-mark" aria-hidden="true">{ }</div>
  <div class="brand-copy"><strong>Code Reviewer</strong><span>AI assisted code analysis</span></div>
  <div class="header-status"><span class="status-dot"></span> Ready to review</div>
</header>
"""


def render_history(items: list[dict]) -> str:
    """Render the session review history as a list of cards (newest first)."""
    if not items:
        return (
            "<div class='hist-empty'>"
            "<span class='hist-empty__icon' aria-hidden='true'>↺</span>"
            "<div class='hist-empty__title'>No reviews yet</div>"
            "<div class='hist-empty__sub'>Runs from this session will appear here.</div>"
            "</div>"
        )

    rows = []
    for it in reversed(items):
        try:
            v = float(it.get("score"))
            score_txt = f"{v:.1f}"
            if v >= 8.5:
                vc = "exc"
            elif v >= 7:
                vc = "good"
            elif v >= 5:
                vc = "fair"
            elif v >= 3:
                vc = "warn"
            else:
                vc = "poor"
        except (TypeError, ValueError):
            score_txt, vc = "—", "muted"

        summary = (it.get("summary") or "").strip() or "No summary."
        rows.append(
            "<div class='hist-card'>"
            f"<div class='hist-score hist-score--{vc}'>{score_txt}</div>"
            "<div class='hist-body'>"
            f"<div class='hist-meta'><span class='hist-lang'>{html.escape(str(it.get('language','')))}</span>"
            f"<span class='hist-mode'>{html.escape(str(it.get('mode','')))}</span>"
            f"<span class='hist-time'>{html.escape(str(it.get('time','')))}</span></div>"
            f"<div class='hist-summary'>{html.escape(summary)}</div>"
            f"<div class='hist-provider'>Reviewed with {html.escape(str(it.get('provider','')))}</div>"
            "</div></div>"
        )
    return "<div class='hist-list'>" + "".join(rows) + "</div>"

# Placeholder shown in every results panel before the first review.
_IDLE = (
    "<p class='empty-note'>Run a review to see this section.</p>"
)


def score_card(value, empty_label: str = "No review yet") -> str:
    """Render a compact score card that works beside the summary."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = None

    if v is None:
        num, verdict, vclass = "—", empty_label, "muted"
    else:
        v = max(0.0, min(10.0, v))
        num = f"{v:.1f}"
        if v >= 8.5:
            verdict, vclass = "Excellent", "exc"
        elif v >= 7:
            verdict, vclass = "Great", "good"
        elif v >= 5:
            verdict, vclass = "Fair", "fair"
        elif v >= 3:
            verdict, vclass = "Needs work", "warn"
        else:
            verdict, vclass = "Poor", "poor"

    return (
        "<div class='score-card'>"
        "<div class='metric-label'>OVERALL SCORE</div>"
        f"<div class='metric-value'>{num}<span>/10</span></div>"
        f"<div class='metric-verdict metric-verdict--{vclass}'>{verdict}</div>"
        "</div>"
    )


SCORE_PLACEHOLDER = score_card(None)


# ---------------------------------------------------------------------------
# Result formatting
# ---------------------------------------------------------------------------

def _findings_markdown(items: list[dict], impact_key: str = "severity") -> str:
    """Render a list of findings as compact Markdown for a results panel."""
    if not items:
        return "<div class='ok-card'>✅ <strong>No issues detected in this category.</strong></div>"
    blocks = []
    for i, it in enumerate(items, 1):
        level = it.get(impact_key) or it.get("impact") or it.get("severity") or "n/a"
        line = it.get("line", "n/a")
        title = it.get("title", "Finding")
        detail = it.get("detail", "")
        fix = it.get("fix", "")
        block = f"### {i}. {title}\n{badge(level)} · `line {line}`\n\n{detail}"
        if fix:
            block += f"\n\n> 💡 **Fix:** {fix}"
        blocks.append(block)
    return "\n\n---\n\n".join(blocks)


def _architecture_markdown(arch: dict) -> str:
    if not arch:
        return "_No architecture assessment available._"
    parts = []
    if arch.get("assessment"):
        parts.append(arch["assessment"])
    for label, key, icon in (
        ("Strengths", "strengths", "✅"),
        ("Concerns", "concerns", "⚠️"),
        ("Suggestions", "suggestions", "💡"),
    ):
        values = arch.get(key) or []
        if values:
            parts.append(f"**{icon} {label}**\n" + "\n".join(f"- {v}" for v in values))
    return "\n\n".join(parts)


def _best_practices_markdown(items: list[dict]) -> str:
    if not items:
        return "<div class='ok-card'>✅ <strong>No additional suggestions from this review.</strong></div>"
    return "\n\n---\n\n".join(
        f"### {i}. {it.get('title', '')}\n{it.get('detail', '')}"
        for i, it in enumerate(items, 1)
    )


def _complexity_markdown(complexity: dict, metrics: dict) -> str:
    return (
        f"| Metric | Value |\n"
        f"| --- | --- |\n"
        f"| Rating | **{complexity.get('rating', '—')}** |\n"
        f"| Time complexity | `{complexity.get('estimated_time_complexity', '—')}` |\n"
        f"| Space complexity | `{complexity.get('estimated_space_complexity', '—')}` |\n"
        f"| Cyclomatic estimate | {complexity.get('cyclomatic_estimate', '—')} |\n"
        f"| Cyclomatic (static) | {metrics.get('cyclomatic_proxy', '—')} |\n"
        f"| Total lines | {metrics.get('total_lines', 0)} |\n"
        f"| Code lines | {metrics.get('code_lines', 0)} |\n"
        f"| Comment lines | {metrics.get('comment_lines', 0)} |\n"
        f"| Branch points | {metrics.get('branch_points', 0)} |\n"
        + (f"\n\n{complexity.get('notes')}" if complexity.get("notes") else "")
    )


def _error_card(title: str, message: str) -> str:
    """A premium, animated error card (styled + shake via CSS)."""
    return (
        "<div class='error-card'>"
        f"<div class='error-card__title'>⚠️ {html.escape(title)}</div>"
        f"<div class='error-card__body'>{html.escape(message)}</div>"
        "</div>"
    )


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

def _error_outputs(title: str, message: str):
    """Populate every output with a graceful error state (shown once)."""
    card = _error_card(title, message)
    notice = (
        "<div class='idle-hint'><span class='idle-dot'></span> "
        "See the summary above for details.</div>"
    )
    return (
        SCORE_PLACEHOLDER,
        card,     # summary (the single, prominent error card)
        notice,   # bugs
        notice,   # security
        notice,   # performance
        notice,   # architecture
        notice,   # best practices
        notice,   # complexity
        gr.update(value="", visible=False),  # refactored
        "",                                   # report
        gr.update(visible=False),            # download
    )


def _idle_outputs():
    """Reset the result area when the source changes or is cleared."""
    return (
        SCORE_PLACEHOLDER,
        "### Summary\n\nRun a review to see the key takeaways.",
        *([_IDLE] * 6),
        gr.update(value="", visible=False),
        "_Your report will appear here after a review._",
        gr.update(value=None, visible=False),
    )


def _result_outputs(data: dict, metrics: dict, provider: str, model: str,
                    language: str, mode: str, report_md: str, report_path: str):
    refactored = data.get("refactored_code", "")
    engine = ""
    if provider:
        model_tag = f" · <code>{html.escape(model)}</code>" if model else ""
        engine = (
            f"<div class='engine-tag'>Reviewed with <strong>{html.escape(provider)}</strong>"
            f"{model_tag}</div>\n\n"
        )
    return (
        score_card(data.get("overall_score"), "AI score unavailable"),
        f"{engine}### Review summary\n\n{data.get('summary', '') or '_No summary provided._'}",
        _findings_markdown(data.get("bugs", []), "severity"),
        _findings_markdown(data.get("security_issues", []), "severity"),
        _findings_markdown(data.get("performance_issues", []), "impact"),
        _architecture_markdown(data.get("architecture_review", {})),
        _best_practices_markdown(data.get("best_practices", [])),
        _complexity_markdown(data.get("complexity", {}), metrics),
        gr.update(value=refactored, language=code_language_token(language), visible=bool(refactored)),
        report_md,
        gr.update(value=report_path, visible=True),
    )


def _history_choices(history: list[dict]) -> list[tuple[str, str]]:
    return [
        (f"{item['time']} · {item['language']} · {item['mode']} · {item.get('score', '—')}/10", item["id"])
        for item in reversed(history)
    ]


def run_review(
    code: str, language: str, mode: str, autodetect: bool, history: list | None
):
    """Gradio callback: run a review and populate every output component."""
    history = list(history or [])

    # Friendly guard before hitting the API.
    if not code or not code.strip():
        yield (
            *_error_outputs(
                "Nothing to review",
                "Paste some code into the editor, then click Review code.",
            ),
            history,
            render_history(history),
            gr.update(),
            gr.update(),
        )
        return

    if len(code) > 30_000:
        yield (*_error_outputs("Code is too long", "Please keep the input under 30,000 characters."),
               history, render_history(history), gr.update(), gr.update())
        return

    # Auto-detect keeps the language in sync with what was actually pasted.
    if autodetect:
        detected, confidence = detect_language(code)
        if confidence >= 0.25:
            language = detected

    try:
        result = review_code(code, language, mode)
    except ReviewError as exc:
        yield (*_error_outputs("Review could not be completed", str(exc)),
               history, render_history(history), gr.update(), gr.update())
        return
    except Exception:  # noqa: BLE001 - never crash the UI
        yield (*_error_outputs("Unexpected error",
                               "Please try again in a moment. Check the server log for details."),
               history, render_history(history), gr.update(), gr.update())
        return

    data = result.data
    metrics = result.metrics
    report_md = render_markdown_report(data, language, mode, metrics, result.provider)

    # Persist the report so it can be downloaded.
    safe_lang = language.replace("/", "-").replace(" ", "_")
    report_path = REPORTS_DIR / f"code_review_{safe_lang}_{uuid4().hex[:12]}.md"
    report_path.write_text(report_md, encoding="utf-8")

    # Record this review in the session history (newest rendered first).
    history.append(
        {
            "id": uuid4().hex,
            "time": _dt.datetime.now().strftime("%H:%M"),
            "language": language,
            "mode": mode,
            "score": data.get("overall_score"),
            "summary": textwrap.shorten(data.get("summary") or "", width=140, placeholder="…"),
            "provider": result.provider or "—",
            "model": result.model,
            "code": code,
            "data": data,
            "metrics": metrics,
            "report": report_md,
            "report_path": str(report_path),
        }
    )
    history = history[-20:]

    yield (
        *_result_outputs(data, metrics, result.provider, result.model, language,
                         mode, report_md, str(report_path)),
        history,
        render_history(history),
        gr.update(choices=_history_choices(history), value=history[-1]["id"]),
        gr.update(interactive=True),
    )


def restore_review(review_id: str, history: list[dict] | None):
    item = next((it for it in history or [] if it.get("id") == review_id), None)
    if not item:
        return (*_idle_outputs(), gr.update(), gr.update(), gr.update(),
                gr.update(selected="review"))
    return (
        *_result_outputs(item["data"], item["metrics"], item["provider"],
                         item["model"], item["language"], item["mode"],
                         item["report"], item["report_path"]),
        gr.update(value=item["code"], language=code_language_token(item["language"])),
        gr.update(value=item["language"]),
        gr.update(value=item["mode"]),
        gr.update(selected="review"),
    )


def clear_workspace():
    return (gr.update(value=""), gr.update(value=""), *_idle_outputs())


def load_sample():
    return (gr.update(value=PLACEHOLDER_CODE, language="python"),
            gr.update(value="Python"), *_idle_outputs())


def on_language_change(language: str):
    """Update the editor's syntax highlighting when the language changes."""
    return gr.update(language=code_language_token(language))


def autodetect_on_edit(code: str, enabled: bool, current_lang: str):
    """Live language detection as the user edits, without disrupting typing."""
    if not enabled or not code or not code.strip():
        return gr.update(), gr.update(), gr.update(value="")

    lang, confidence = detect_language(code)
    pct = int(confidence * 100)
    if confidence < 0.25:
        status = "🪄 <span class='detect-muted'>Auto-detect: not enough signal yet…</span>"
        return gr.update(), gr.update(), gr.update(value=status)

    status = f"🪄 Detected <strong>{lang}</strong> · <span class='detect-pct'>{pct}%</span>"
    if lang == current_lang:
        return gr.update(), gr.update(), gr.update(value=status)

    # Switch language + editor highlighting; value unchanged so no edit loop.
    return (
        gr.update(value=lang),
        gr.update(language=code_language_token(lang)),
        gr.update(value=status),
    )


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def build_app() -> gr.Blocks:
    with gr.Blocks(
        title="AI Code Reviewer Pro",
        theme=gr.themes.Base(
            primary_hue=gr.themes.colors.indigo,
            neutral_hue=gr.themes.colors.gray,
        ),
        css=THEME_CSS,
        head=HEAD_HTML,
    ) as demo:
        history_state = gr.State([])
        gr.HTML(BRAND_HTML, elem_classes="header-shell")

        with gr.Tabs(selected="review", elem_classes="primary-tabs") as main_tabs:
            with gr.Tab("Review workspace", id="review"):
                gr.HTML(
                    "<div class='page-intro'>"
                    "<div><span class='eyebrow'>AI CODE REVIEWER PRO</span>"
                    "<h1>Review code with confidence.</h1>"
                    "<p>Find issues, understand tradeoffs, and leave with a clearer implementation.</p></div>"
                    "<span class='intro-step'>01 / 02 &nbsp; ADD CODE</span>"
                    "</div>",
                    elem_classes="intro-shell",
                )

                with gr.Row(equal_height=False, elem_classes="workspace"):
                    with gr.Column(scale=5, min_width=340, elem_classes="input-column"):
                        gr.HTML(
                            "<div class='section-heading'><span class='section-number'>01</span>"
                            "<div><h2>Your code</h2><p>Paste a snippet and choose how to review it.</p></div></div>"
                        )
                        with gr.Column(elem_classes="surface editor-surface"):
                            with gr.Row(elem_classes="settings-row"):
                                language = gr.Dropdown(
                                    LANGUAGES, value="Python", label="Language",
                                    filterable=False, elem_classes="field-select",
                                    min_width=150,
                                )
                                mode = gr.Dropdown(
                                    REVIEW_MODE_NAMES, value="General", label="Review focus",
                                    filterable=False, elem_classes="field-select",
                                    min_width=150,
                                )
                            code = gr.Code(
                                value="", language="python", lines=16,
                                show_label=False, container=False,
                                elem_classes="code-input",
                            )
                            with gr.Row(elem_classes="assist-row"):
                                autodetect = gr.Checkbox(
                                    value=True, label="Auto-detect language",
                                    elem_classes="detect-check",
                                )
                                detect_status = gr.Markdown("", elem_classes="detect-status")
                            with gr.Row(elem_classes="actions-row"):
                                review_btn = gr.Button(
                                    "Review code", variant="primary",
                                    elem_classes="review-btn", scale=3,
                                )
                                sample_btn = gr.Button(
                                    "Try sample", elem_classes="secondary-btn", scale=1,
                                )
                                clear_btn = gr.Button(
                                    "Clear", elem_classes="secondary-btn", scale=1,
                                )
                        gr.HTML(
                            "<p class='privacy-note'>Without an API key, a limited static scan runs locally. "
                            "AI reviews send your code to the configured provider.</p>"
                        )

                    with gr.Column(scale=7, min_width=430, elem_classes="result-column"):
                        gr.HTML(
                            "<div class='section-heading'><span class='section-number'>02</span>"
                            "<div><h2>Review results</h2><p>Start with the summary, then explore each finding.</p></div></div>"
                        )
                        with gr.Row(equal_height=False, elem_classes="overview-row"):
                            score = gr.HTML(SCORE_PLACEHOLDER, elem_classes="surface score-surface")
                            summary = gr.Markdown(
                                "### Summary\n\nRun a review to see the key takeaways.",
                                elem_classes="surface summary-surface",
                            )
                        with gr.Column(elem_classes="surface results-surface"):
                            gr.HTML("<h3 class='result-group-title'>Findings</h3>")
                            gr.HTML("<h4 class='result-section-title'>Bugs</h4>")
                            bugs_out = gr.Markdown(_IDLE, elem_classes="result-content")
                            gr.HTML("<h4 class='result-section-title'>Security</h4>")
                            security_out = gr.Markdown(_IDLE, elem_classes="result-content")
                            gr.HTML("<h4 class='result-section-title'>Performance</h4>")
                            performance_out = gr.Markdown(_IDLE, elem_classes="result-content")
                        with gr.Column(elem_classes="surface results-surface"):
                            gr.HTML("<h3 class='result-group-title'>Code quality</h3>")
                            gr.HTML("<h4 class='result-section-title'>Architecture</h4>")
                            architecture_out = gr.Markdown(_IDLE, elem_classes="result-content")
                            gr.HTML("<h4 class='result-section-title'>Standards</h4>")
                            best_out = gr.Markdown(_IDLE, elem_classes="result-content")
                            gr.HTML("<h4 class='result-section-title'>Complexity</h4>")
                            complexity_out = gr.Markdown(_IDLE, elem_classes="result-content")
                        with gr.Column(elem_classes="surface results-surface"):
                            gr.HTML("<h3 class='result-group-title'>Refactored code</h3>")
                            gr.Markdown("AI reviews can include a suggested rewrite.", elem_classes="tab-helper")
                            refactored_out = gr.Code(
                                label="Suggested code", language="python",
                                visible=False, elem_classes="refactor-code",
                            )
                        with gr.Column(elem_classes="surface results-surface"):
                            gr.HTML("<h3 class='result-group-title'>Full report</h3>")
                            download_btn = gr.DownloadButton(
                                "Download Markdown report",
                                visible=False, elem_classes="download-btn",
                            )
                            with gr.Accordion("Read full report", open=False, elem_classes="report-accordion"):
                                report_out = gr.Markdown(
                                    "_Your complete report will appear here._",
                                    elem_classes="report-content",
                                )

            with gr.Tab("History", id="history"):
                gr.HTML(
                    "<div class='history-intro'><span class='eyebrow'>THIS SESSION</span>"
                    "<h1>Review history</h1>"
                    "<p>Reopen a previous result and continue from its code.</p></div>"
                )
                with gr.Row(elem_classes="history-controls"):
                    history_picker = gr.Dropdown(
                        choices=[], label="Choose a review", interactive=True,
                        elem_classes="history-picker",
                    )
                    reopen_btn = gr.Button(
                        "Open review", variant="primary",
                        elem_classes="reopen-btn", interactive=False,
                    )
                history_view = gr.HTML(render_history([]), elem_classes="history-view")

        gr.HTML("<div class='app-footer'>AI Code Reviewer Pro <span>·</span> Clear feedback for better code.</div>")

        outputs = [
            score, summary, bugs_out, security_out, performance_out,
            architecture_out, best_out, complexity_out, refactored_out,
            report_out, download_btn, history_state, history_view,
            history_picker, reopen_btn,
        ]
        review_btn.click(
            fn=run_review,
            inputs=[code, language, mode, autodetect, history_state],
            outputs=outputs,
            api_name="review",
            show_progress="full",
        )
        for source in (code, language, mode):
            source.input(fn=_idle_outputs, inputs=None, outputs=outputs[:11], queue=False)
        code.change(
            fn=autodetect_on_edit,
            inputs=[code, autodetect, language],
            outputs=[language, code, detect_status],
            show_progress="hidden",
        )
        language.change(fn=on_language_change, inputs=language, outputs=code)
        clear_btn.click(
            fn=clear_workspace, inputs=None,
            outputs=[code, detect_status, *outputs[:11]], queue=False,
        )
        sample_btn.click(
            fn=load_sample, inputs=None,
            outputs=[code, language, *outputs[:11]], queue=False,
        )
        reopen_btn.click(
            fn=restore_review, inputs=[history_picker, history_state],
            outputs=[*outputs[:11], code, language, mode, main_tabs], queue=False,
        )

    return demo


def main() -> None:
    # Free hosts (Render, Koyeb, Cloud Run, Railway) inject the port via $PORT.
    port = int(os.getenv("PORT", os.getenv("GRADIO_SERVER_PORT", "7860")))
    demo = build_app()
    demo.queue(default_concurrency_limit=4).launch(
        server_name=os.getenv("GRADIO_SERVER_NAME", "0.0.0.0"),
        server_port=port,
        show_api=False,
    )


if __name__ == "__main__":
    main()
