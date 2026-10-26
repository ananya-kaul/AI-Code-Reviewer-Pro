---
title: AI Code Reviewer Pro
emoji: ⌘
colorFrom: indigo
colorTo: purple
sdk: gradio
sdk_version: 5.50.0
app_file: app.py
pinned: false
license: mit
---

# AI Code Reviewer Pro

A multi-language **AI code review** application with a clean,
light-only UI. Paste code, pick a language and a review lens, and get a structured,
principal-level review — bugs, security, performance, architecture,
best-practices, complexity analysis, and a fully refactored version — all
rendered as an interactive report you can copy or download.

Provider-agnostic: run it on **Groq, Google Gemini, or OpenRouter for free**
(or OpenAI), with careful prompt engineering.

---

## ✨ Features

- **Light-only, responsive UI** built with Gradio Blocks and custom CSS.
- **Session history:** reopen any of the last 20 reviews, including its code and report.
- **Safer review flow:** empty and oversized inputs are caught before an API call, and every download gets a unique filename.
- **Sample snippet** to explore the app without supplying your own code.
- **Works without an API key:** local static checks flag selected Python syntax,
  mutable defaults, recursion, bare exception handlers, and dynamic execution;
  JavaScript/TypeScript `eval` and SQL `SELECT *` are also flagged. The local
  scan is limited and does not assign an AI score or generate a rewrite.
- **Syntax-highlighted code editor** that adapts to the selected language.
- **15 languages:** Swift, Dart, Python, JavaScript, TypeScript, Java, Kotlin,
  Go, Rust, C#, C++, PHP, Ruby, SQL, HTML/CSS.
- **8 review modes:** General, Bugs, Performance, Security, Best Practices,
  Clean Architecture, Refactoring, Testing.
- **Structured output:**
  - Overall score out of 10
  - Bugs (with severity, line, and suggested fix)
  - Security issues
  - Performance issues
  - Architecture review (strengths, concerns, suggestions)
  - Best-practice suggestions
  - Complexity analysis (time/space Big-O, cyclomatic estimate + static proxy)
  - Refactored code
- **Markdown report** with copy and one-click **download**.

---

## 🚀 Quick start

### 1. Clone and install

```bash
git clone https://github.com/ananya-kaul/AI-Code-Reviewer-Pro.git
cd AI-Code-Reviewer-Pro
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure a provider key for a full AI review (optional)

The app starts without a key and runs limited static checks locally. For a full
AI review, it auto-detects which provider to use from whichever API key is
present. You only need **one**:

| Provider | Cost | Get a key |
| --- | --- | --- |
| **Groq** (recommended) | **Free**, no card | https://console.groq.com/keys |
| **Google Gemini** | **Free** tier | https://aistudio.google.com/app/apikey |
| **OpenRouter** | **Free** `:free` models | https://openrouter.ai/keys |
| OpenAI | Paid | https://platform.openai.com/api-keys |

```bash
cp .env.example .env
# then edit .env and set ONE key, e.g. GROQ_API_KEY=gsk_...
```

Or export it directly:

```bash
export GROQ_API_KEY="gsk_your_free_key_here"
```

### 3. Run

```bash
python app.py
```

Open the printed local URL (default `http://localhost:7860`).

---

## ⚙️ Configuration

| Variable              | Default            | Description                                    |
| --------------------- | ------------------ | ---------------------------------------------- |
| `GROQ_API_KEY`        | —                  | Groq key (free). Auto-detected first.          |
| `GEMINI_API_KEY`      | —                  | Google Gemini key (free).                      |
| `OPENROUTER_API_KEY`  | —                  | OpenRouter key (free models).                  |
| `OPENAI_API_KEY`      | —                  | OpenAI key (paid).                             |
| `LLM_PROVIDER`        | _(auto)_           | Force `groq` \| `gemini` \| `openrouter` \| `openai`. |
| `LLM_MODEL`           | _(provider default)_ | Override the model for the active provider.  |
| `GRADIO_SERVER_NAME`  | `0.0.0.0`          | Host interface to bind.                         |
| `GRADIO_SERVER_PORT`  | `7860`             | Port to serve on.                               |

> For AI reviews, set **one** provider key. Auto-detection order: Groq → Gemini → OpenRouter → OpenAI.

---

## 🧱 Project structure

```
AI-Code-Reviewer-Pro/
├── app.py            # Gradio UI and callbacks
├── reviewer.py       # Review engine (multi-provider, OpenAI-compatible)
├── prompts.py        # Prompt engineering + JSON contract
├── utils.py          # Catalogs, metrics, Markdown rendering
├── theme.css         # Light responsive theme
├── requirements.txt
├── README.md
├── .env.example
├── .gitignore
└── assets/           # Logo and generated reports
```

## 🏗️ How it works

1. `prompts.py` builds a strict system + user prompt, including a JSON schema
   the model must satisfy and a mode-specific review lens.
2. `reviewer.py` calls the active provider's OpenAI-compatible API when a key is
   available, or runs limited static checks locally. Provider responses are
   normalized so every expected field is present. Static metrics are computed locally.
3. `app.py` renders the structured result into a tabbed, light UI and a
   downloadable Markdown report.

Review history is kept in the current browser session. Generated reports are
stored under `assets/reports/` on the server and excluded from Git.

---

## 🌐 Deploy (free)

### Render (recommended — one click)

This repo ships a [`render.yaml`](render.yaml) Blueprint.

1. Go to **https://dashboard.render.com/blueprints** → **New Blueprint Instance**.
2. Connect this GitHub repo (`ananya-kaul/AI-Code-Reviewer-Pro`).
3. Render reads `render.yaml` and provisions a free web service.
4. In the service's **Environment**, set `OPENAI_API_KEY` to your key and click
   **Save** (this triggers a deploy). `OPENAI_MODEL` defaults to `gpt-4o-mini`.

> The free plan sleeps after inactivity and cold-starts on the next visit.

### Docker (Koyeb, Fly.io, Cloud Run, Railway, or local)

A portable [`Dockerfile`](Dockerfile) is included:

```bash
docker build -t ai-code-reviewer-pro .
docker run -p 7860:7860 -e OPENAI_API_KEY=sk-your-key ai-code-reviewer-pro
```

The app binds to `$PORT` when the host provides one, so it works on any
container platform out of the box.

### Hugging Face Spaces

The repo is also Spaces-ready (see the YAML header above). Note that hosting a
**Gradio** Space on HF's free CPU tier currently requires a PRO subscription;
create the Space, push this repo, and set `OPENAI_API_KEY` as a **Space secret**.

---

## 📝 License

Released under the [MIT License](#).
