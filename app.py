"""AI Code Reviewer Pro — Gradio application entry point.

A light, responsive UI for reviewing source code across many
languages and review lenses. The heavy lifting lives in `reviewer.py`; this
module wires the interface, formats results, and handles errors gracefully.
"""

from __future__ import annotations

import datetime as _dt
import html
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
MICRO_JS = (APP_DIR / "assets" / "interactions.js").read_text(encoding="utf-8")
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

# Injected once into <head>; fonts, icon set, and micro-interaction script.
HEAD_HTML = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?'
    "family=Inter:wght@400;500;600;700;900&"
    "family=JetBrains+Mono:wght@400;500;700&"
    'family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@24,400,0,0&display=swap" '
    'rel="stylesheet">'
    f"<script>{MICRO_JS}</script>"
)


def _icon(name: str, cls: str = "") -> str:
    """Render a Material Symbols icon span."""
    return f"<span class='material-symbols-outlined {cls}'>{name}</span>"


BRAND_HTML = f"""
<div class="topnav__brand">
  <div class="topnav__logo">{_icon('terminal')}</div>
  <span class="topnav__name">AI Code Reviewer Pro</span>
</div>
"""

NAV_RIGHT_HTML = f"""
<div class="topnav__right">
  <div class="live-pill"><span class="live-dot"></span> Ready for review</div>
</div>
"""

FOOTER_HTML = """
<footer class="site-footer">
  <div class="site-footer__left">
    <p class="site-footer__copy">AI Code Reviewer Pro · Review code with a clear next step.</p>
  </div>
</footer>
"""


def render_history(items: list[dict]) -> str:
    """Render the session review history as a list of cards (newest first)."""
    if not items:
        return (
            "<div class='hist-empty'>"
            f"{_icon('history', 'hist-empty__icon')}"
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
    "<div class='idle-hint'><span class='idle-dot'></span> Awaiting review — "
    "run a review to populate this panel.</div>"
)


def score_card(value) -> str:
    """Render the overall score as a circular gauge card."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = None

    if v is None:
        pct, num, verdict, vclass = 0.0, "—", "Awaiting review", "muted"
    else:
        v = max(0.0, min(10.0, v))
        pct = v / 10 * 100
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
        "<div class='score-hero'>"
        "<div class='score-cap'>Overall score</div>"
        f"<div class='score-ring score-ring--{vclass}' style='--pct:{pct:.1f}%' "
        f"data-score='{num}'>"
        "<div class='score-ring__inner'>"
        f"<span class='score-num'>{num}</span>"
        "<span class='score-den'>/ 10</span>"
        "</div></div>"
        f"<div class='score-verdict score-verdict--{vclass}'>{verdict}</div>"
        "</div>"
    )


SCORE_PLACEHOLDER = score_card(None)


# ---------------------------------------------------------------------------
# Result formatting
# ---------------------------------------------------------------------------

def _findings_markdown(items: list[dict], impact_key: str = "severity") -> str:
    """Render a list of findings as compact Markdown for a results panel."""
    if not items:
        return "<div class='ok-card'>✅ <strong>No issues found in this category.</strong></div>"
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
        return "<div class='ok-card'>✅ <strong>Code already follows the key best practices.</strong></div>"
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
        f"| Cyclomatic (model) | {complexity.get('cyclomatic_estimate', '—')} |\n"
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
        "<div class='idle-hint'>Add code and run a review to see your summary.</div>",
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
        engine = (
            f"<div class='engine-tag'>Reviewed with <strong>{html.escape(provider)}</strong>"
            f" · <code>{html.escape(model)}</code></div>\n\n"
        )
    return (
        score_card(data.get("overall_score")),
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
    report_md = render_markdown_report(data, language, mode, metrics)

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
            "summary": (data.get("summary") or "")[:180],
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
                gr.update(visible=True), gr.update(visible=False))
    return (
        *_result_outputs(item["data"], item["metrics"], item["provider"],
                         item["model"], item["language"], item["mode"],
                         item["report"], item["report_path"]),
        gr.update(value=item["code"], language=code_language_token(item["language"])),
        gr.update(value=item["language"]),
        gr.update(value=item["mode"]),
        gr.update(visible=True),
        gr.update(visible=False),
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
            primary_hue=gr.themes.colors.blue,
            neutral_hue=gr.themes.colors.gray,
        ),
        css=THEME_CSS,
        head=HEAD_HTML,
        fill_height=True,
    ) as demo:
        history_state = gr.State([])

        # Top navigation bar (brand · clickable nav · status cluster).
        with gr.Row(elem_classes="topnav", equal_height=True):
            gr.HTML(BRAND_HTML)
            with gr.Row(elem_classes="topnav__links"):
                nav_dashboard = gr.Button(
                    "Dashboard", elem_classes="topnav__link is-active"
                )
                nav_history = gr.Button("History", elem_classes="topnav__link")
            gr.HTML(NAV_RIGHT_HTML)

      # ================= HISTORY VIEW =================
        with gr.Column(visible=False, elem_classes="view-history") as history_col:
            gr.HTML(
                "<div class='view-head'>"
                f"{_icon('history', 'view-head__icon')}"
                "<div><div class='view-head__title'>Review History</div>"
                "<div class='view-head__sub'>Every review you run this session</div>"
                "</div></div>"
            )
            history_picker = gr.Dropdown(
                choices=[], label="Choose a review to reopen", interactive=True,
                elem_classes="history-picker",
            )
            reopen_btn = gr.Button("Open selected review", variant="primary", elem_classes="reopen-btn", interactive=False)
            history_view = gr.HTML(render_history([]))

      # ================= DASHBOARD VIEW =================
        dashboard_col = gr.Column(visible=True, elem_classes="view-dashboard")
        with dashboard_col:
          gr.HTML(
              "<section class='hero'><div class='hero-kicker'>YOUR CODE, CLEARER</div>"
              "<h1>A better review starts here.</h1>"
              "<p>Paste a snippet, choose a focus, and turn findings into practical fixes.</p>"
              "</section>"
          )
          with gr.Row(equal_height=False, elem_classes="workspace"):
            # ============ LEFT: Code Editor ============
            with gr.Column(scale=6, min_width=420, elem_classes="col-editor"):
                with gr.Group(elem_classes="panel editor-card panel--in"):
                    gr.HTML(
                        "<div class='editor-titlebar'>"
                        f"{_icon('code', 'editor-title__icon')}"
                        "<span>Source code</span><span class='editor-titlebar__hint'>Paste a snippet to begin</span></div>"
                    )
                    with gr.Row(elem_classes="editor-toolbar", equal_height=True):
                        language = gr.Dropdown(
                            LANGUAGES,
                            value="Python",
                            label="Language",
                            show_label=False,
                            elem_classes="control toolbar-select",
                            min_width=120,
                            scale=1,
                        )
                        mode = gr.Dropdown(
                            REVIEW_MODE_NAMES,
                            value="General",
                            label="Review mode",
                            show_label=False,
                            elem_classes="control toolbar-select",
                            min_width=120,
                            scale=1,
                        )
                    code = gr.Code(
                        value="",
                        language="python",
                        label="Your code",
                        lines=18,
                        elem_classes="code-editor",
                    )
                    with gr.Row(elem_classes="editor-actions"):
                        review_btn = gr.Button(
                            "Review code",
                            variant="primary",
                            elem_classes="review-btn",
                            scale=3,
                        )
                        sample_btn = gr.Button(
                            "Try sample", elem_classes="ghost-btn", scale=1
                        )
                        clear_btn = gr.Button(
                            "Clear", elem_classes="ghost-btn", scale=1
                        )

                with gr.Group(elem_classes="panel detect-card panel--in"):
                    with gr.Row(elem_classes="detect-bar", equal_height=True):
                        gr.HTML(
                            "<div class='detect-label'>"
                            f"{_icon('auto_fix_high', 'detect-label__icon')}"
                            "<div><div class='detect-title'>Auto-detect language</div>"
                            "<div class='detect-sub'>Suggest a language as you type</div>"
                            "</div></div>"
                        )
                        autodetect = gr.Checkbox(
                            value=True,
                            label="",
                            elem_classes="detect-toggle",
                            container=False,
                            scale=0,
                            min_width=60,
                        )
                    detect_status = gr.Markdown("", elem_classes="detect-status")

            # ============ MIDDLE: Score + Summary ============
            with gr.Column(scale=4, min_width=240, elem_classes="col-mid"):
                score = gr.HTML(
                    SCORE_PLACEHOLDER,
                    elem_classes="panel score-panel panel--in",
                )
                summary = gr.Markdown(
                    "<div class='card-head'>"
                    f"{_icon('summarize', 'card-head__msicon')}"
                    "<span class='card-head__title'>Summary</span></div>"
                    "<div class='idle-hint'>Add code and run a review to see your summary.</div>",
                    elem_classes="panel summary-panel panel--in",
                )

            # ============ RIGHT: Analysis Tabs ============
            with gr.Column(scale=6, min_width=360, elem_classes="col-tabs"):
                with gr.Group(elem_classes="panel tabs-panel panel--in"):
                  with gr.Tabs(elem_classes="result-tabs"):
                    with gr.Tab("Bugs"):
                        bugs_out = gr.Markdown(_IDLE)
                    with gr.Tab("Security"):
                        security_out = gr.Markdown(_IDLE)
                    with gr.Tab("Performance"):
                        performance_out = gr.Markdown(_IDLE)
                    with gr.Tab("Architecture"):
                        architecture_out = gr.Markdown(_IDLE)
                    with gr.Tab("Standards"):
                        best_out = gr.Markdown(_IDLE)
                    with gr.Tab("Complexity"):
                        complexity_out = gr.Markdown(_IDLE)
                    with gr.Tab("Refactored"):
                        refactored_out = gr.Code(
                            label="Refactored code",
                            language="python",
                            visible=False,
                        )
                    with gr.Tab("Report"):
                        report_out = gr.Markdown(
                            "_The full Markdown report will appear here._",
                            elem_classes="report-panel",
                        )
                        download_btn = gr.DownloadButton(
                            "Download Markdown report",
                            visible=False,
                            elem_classes="download-btn",
                        )

        gr.HTML(FOOTER_HTML)

        # -------------------- Wiring --------------------
        outputs = [
            score,
            summary,
            bugs_out,
            security_out,
            performance_out,
            architecture_out,
            best_out,
            complexity_out,
            refactored_out,
            report_out,
            download_btn,
            history_state,
            history_view,
            history_picker,
            reopen_btn,
        ]

        review_btn.click(
            fn=run_review,
            inputs=[code, language, mode, autodetect, history_state],
            outputs=outputs,
            api_name="review",
            show_progress="full",
        )

        # A result always belongs to the source and settings used for that run.
        # Hide it as soon as the user edits those inputs.
        for source in (code, language, mode):
            source.input(fn=_idle_outputs, inputs=None, outputs=outputs[:11], queue=False)

        # Live auto-detection as the user edits.
        code.change(
            fn=autodetect_on_edit,
            inputs=[code, autodetect, language],
            outputs=[language, code, detect_status],
            show_progress="hidden",
        )

        language.change(fn=on_language_change, inputs=language, outputs=code)

        clear_btn.click(
            fn=clear_workspace,
            inputs=None,
            outputs=[code, detect_status, *outputs[:11]],
            queue=False,
        )
        sample_btn.click(
            fn=load_sample,
            inputs=None,
            outputs=[code, language, *outputs[:11]],
            queue=False,
        )
        reopen_btn.click(
            fn=restore_review,
            inputs=[history_picker, history_state],
            outputs=[*outputs[:11], code, language, mode, dashboard_col, history_col],
            queue=False,
        )

        # -------- Nav view switching (Dashboard <-> History) --------
        # queue=False -> instant UI toggle, bypasses the inference queue.
        nav_dashboard.click(
            fn=lambda: (gr.update(visible=True), gr.update(visible=False)),
            inputs=None,
            outputs=[dashboard_col, history_col],
            queue=False,
        )
        nav_history.click(
            fn=lambda: (gr.update(visible=False), gr.update(visible=True)),
            inputs=None,
            outputs=[dashboard_col, history_col],
            queue=False,
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
