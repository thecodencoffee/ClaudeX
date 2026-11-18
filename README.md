<p style="color: #d1242f; text-align: center;"><strong>UNDER DEVELOPMENT<br>Some features may be incomplete or may not work.<br>Use at your own risk.</strong></p>

# ClaudeX — NVIDIA Nemotron Research Workspace

ClaudeX is a local-first research assistant with a Python backend, a browser interface, a Rust desktop client, and an Android client. Chat inference uses NVIDIA-hosted Nemotron models. The project includes persistent conversations, web search, source retrieval, symbolic mathematics, streamed answers, and research tools.

> This is a complete runnable starter application, not a claim of 99% parity with Claude or any other commercial assistant. Model quality, API limits, web coverage, and tool reliability depend on the selected NVIDIA model and endpoint access.

## Project layout

| Path | Responsibility |
| --- | --- |
| `app.py` | FastAPI application, chat orchestration, API routes, model configuration, and SQLite persistence. |
| `tools/web_search.py` | Web search providers, parallel search, page fetching, and result extraction. |
| `tools/research.py` | Research tool registry, safe source retrieval, symbolic math, and numerical experiments. |
| `tools/file_services.py` | Upload parsing and PDF generation. |
| `tools/lazy_import.py` | Deferred Python imports for optional or expensive dependencies. |
| `static/` | Browser client: HTML, CSS, JavaScript, and brand assets. |
| `tests/` | API configuration and model-control tests. |
| `apps/desktop/iced/` | Rust/Iced desktop launcher for Windows and Linux; starts the Python backend and embeds the web client. |
| `apps/android/` | Rust/Slint Android client; connects to the Python backend over HTTPS. |
| `requirements*.txt` | Runtime, development, and desktop packaging dependencies. |
| `build_desktop_backend.py` | Packages the Python backend sidecar for desktop builds. |
| `data/` | Local database and generated files for browser/server runs; do not commit user data. |

## Features

| Area | What ClaudeX provides |
| --- | --- |
| Chat modes | Chat, Research, and Council modes with streamed responses. |
| NVIDIA inference | Allowlisted NVIDIA Nemotron endpoints, model-specific reasoning controls, and local API-key storage in `.env`. |
| Web research | Live parallel web search and fetch tools available in all chat modes, with visible activity and saved per-turn source snapshots. |
| Council | Two concurrent reviewer calls, with partial-review status when a reviewer is unavailable. Reviewer output is treated as a hypothesis for the lead model to assess. |
| Math and experiments | Restricted SymPy operations and repeatable formula comparisons over a bounded input range. |
| Conversation history | SQLite-backed conversations, messages, research events, and memory. |
| Memory | Reviewable, deletable long-term notes; extraction prompt excludes secrets and sensitive categories. |
| Files | PDF, DOCX, XLSX, PPTX, text/code, HTML, and image uploads; image understanding requires the Super VL model. |
| Browser/desktop output | Markdown, code, MathJax equations, horizontally scrollable tables, and downloadable PDFs. |
| Desktop | Iced shell for Windows and Linux; Linux builds require WebKitGTK 4.1 development files. |
| Android | Native Slint client for Android phones and tablets; connects to the remotely hosted Python backend over HTTPS. |

## NVIDIA models available in ClaudeX

These are the four model IDs currently allowlisted in `app.py` (checked against NVIDIA Build on 2026-11-18). Availability, rate limits, and API behavior depend on NVIDIA and the account's endpoint access.

| Display name | NVIDIA model ID | Intended use in ClaudeX | Effort options |
| --- | --- | --- | --- |
| Nemotron 3.5 Lightning (default) | [`nvidia/nemotron-3.5-lightning-30b-a3b`](https://build.nvidia.com/nvidia/nemotron-3.5-lightning-30b-a3b) | General chat, research, and tool workflows. | Low, Medium, High, Extra, Max |
| Nemotron 3 Ultra | [`nvidia/nemotron-3-ultra-550b-a55b`](https://build.nvidia.com/nvidia/nemotron-3-ultra-550b-a55b) | Deep reasoning and demanding multi-step tasks. | Off, Medium, High |
| Nemotron 3.5 Bolt | [`nvidia/nemotron-3.5-bolt-e4b`](https://build.nvidia.com/nvidia/nemotron-3.5-bolt-e4b) | Lightweight text chat and tool workflows. | Off, On |
| Nemotron 3.5 Super VL | [`nvidia/nemotron-3.5-super-vl-120b-a12b`](https://build.nvidia.com/nvidia/nemotron-3.5-super-vl-120b-a12b) | Image and document understanding as well as text. | Off, Low, High |

The model picker only exposes IDs in the application allowlist. NVIDIA's [live model catalog](https://build.nvidia.com/models?q=nemotron) may include other models, but they are not automatically selectable in ClaudeX.

Council reviewer models are configured separately from the chat model picker:

| Reviewer | Default NVIDIA API model ID | NVIDIA Build availability checked 2026-11-18 |
| --- | --- | --- |
| Assumptions and logic | `qwen/qwen3-next-80b-a3b-thinking` | NVIDIA marks the endpoint deprecated; no partner endpoint is listed. |
| Implementation and systems | `qwen/qwen3.5-122b-a10b` | Free endpoint is deprecated; partner endpoint is listed as available. |

Override these defaults with `COUNCIL_REVIEWER_LOGIC_MODEL` and `COUNCIL_REVIEWER_SYSTEMS_MODEL` in `.env`. Unless the configured reviewer endpoints are available to your account, Council may complete with partial reviews. Reviewer calls are additional inference and can increase latency and API usage. Check NVIDIA's [Qwen 3 Next endpoint](https://build.nvidia.com/qwen/qwen3-next-80b-a3b-thinking) and [Qwen 3.5 endpoint](https://build.nvidia.com/qwen/qwen3.5-122b-a10b) before relying on Council.

## Install on Gentoo Linux

### 1. Requirements

- Gentoo Linux
- Python 3.11 or newer
- Internet access
- An NVIDIA API key from [NVIDIA Build](https://build.nvidia.com/)

Check Python:

```bash
python3 --version
```

### 2. Extract and install

Extract the downloaded ZIP, then enter the `ClaudeX` directory:

```bash
cd ClaudeX
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If your Gentoo Python installation does not include `venv`, install the matching Python package/features for your selected Python version using Portage.

### 3. Add your NVIDIA key

Start the application once and use **Settings** to paste your key, or create `.env` manually:

```bash
cp .env.example .env
nano .env
chmod 600 .env
```

Example:

```env
NVIDIA_API_KEY=nvapi-your-real-key
NVIDIA_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b
NVIDIA_REASONING_EFFORT=Medium
AUTO_THINK=true
AUTO_MEMORY=true
```

Never publish `.env`, paste the key into a chat, commit it to Git, or put it in browser JavaScript.

### 4. Start ClaudeX

```bash
source .venv/bin/activate
python app.py
```

Open **http://127.0.0.1:8765** in your browser.

## Desktop app (Linux and Windows)

The Iced shell is written in Rust and runs the existing Python/FastAPI backend locally. The server binds to a free loopback port chosen at launch, and its database and `.env` are stored in the operating system's ClaudeX application-data directory. Browser mode continues to use the project `data/` directory and `.env`.

Build requirements:

- Linux: Rust, Node.js/npm, Python 3.11+, GTK 3, WebKitGTK 4.1, and the distribution's Iced/Wry build prerequisites.
- Windows: Rust, Node.js/npm, Python 3.11+, Microsoft C++ Build Tools, and WebView2 Runtime.

From the repository root, create a Python environment, install `requirements-desktop-build.txt`, and run `python build_desktop_backend.py`. Then:

```bash
cd apps/desktop
npm install
npm run desktop:dev
# For an installer: npm run desktop:build
```

Build the Python sidecar separately on each target operating system; Windows binaries must be built on Windows and Linux binaries on Linux. The sidecar script rejects an executable larger than 250 MiB, but that is not a measurement of the final installer. The complete installer must be measured on each platform before claiming it meets the 200–250 MiB total-app target. The current host is missing WebKitGTK 4.1, so it cannot produce or run the Linux Iced/Wry build yet.

## Mobile and tablet

| Target | Implementation and current scope |
| --- | --- |
| Android phones | Native Rust/Slint app. Connects to the Python backend over HTTPS; supports chat listing/creation, Chat/Research/Council modes, and streamed text replies. Physical-device verification is still pending. |
| Android tablets | Intended to use the same Android app and Slint screen. Tablet-specific layout tuning and physical-device verification are still pending. |
| iOS | Not supported or included. |

The Android app does not run or bundle the Python backend. The backend must be reachable over HTTPS. On first launch, enter its HTTPS origin and access token. The token is kept in Android private app storage and sent as a bearer credential. Remote API requests require the configured token; the server refuses non-loopback binding without a token of at least 32 characters. Native HTTP requests do not use browser CORS.

The Android client is an early implementation and does not yet include all desktop features, including file attachments, generated files, and full settings management. Android packaging and device verification are still pending; no APK has been built.

To expose a self-hosted backend directly, set `CLAUDEX_HOST=0.0.0.0` and a unique `CLAUDEX_ACCESS_TOKEN` of at least 32 characters in its environment or `.env`, and put the server behind HTTPS. The token is per deployment; rotate it and update connected devices together. For a reverse proxy, enable trusted proxy-header handling only for the proxy you control. Do not expose the API over plain HTTP.

Generate a token with `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'`, save it as `CLAUDEX_ACCESS_TOKEN` on the server, and start the service with `CLAUDEX_HOST=0.0.0.0 python app.py` behind your TLS reverse proxy. In the mobile app, enter the HTTPS origin and that token. The API also checks that the token is present for non-loopback requests, even if the process was started with a different Uvicorn command.

The Android project is in `apps/android`. Build prerequisites are Android API 35, NDK r30 or later, the Rust Android targets, and `cargo-apk2`. Install the Cargo tools and targets with:

```bash
cargo install cargo-apk2 --locked
rustup target add aarch64-linux-android armv7-linux-androideabi x86_64-linux-android
```

From `apps/desktop`, `npm run android:dev` builds, installs, and launches on a connected device; `npm run android:build` creates a release APK. Deploying the Python service and signing any eventual release package remain separate operations.

Alternative:

```bash
uvicorn app:app --host 127.0.0.1 --port 8765
```

The server binds to `127.0.0.1` by default. Do not change it to `0.0.0.0` unless you add authentication, TLS, CSRF protection, and other deployment hardening.

## How persistent memory works

1. Every user and assistant message is stored in the local SQLite conversation database.
2. At the end of an exchange, if Persistent memory is enabled, ClaudeX asks the configured NVIDIA model to identify up to three durable, useful facts explicitly stated by the user.
3. The extraction prompt excludes secrets and sensitive personal categories.
4. Saved items appear under **Persistent memory**. You can delete an individual item or clear the entire memory store.
5. Relevant saved memories are inserted into later model context. The full history of other conversations is retained in SQLite but is not indiscriminately sent to every prompt.

Memory extraction is model-generated and imperfect. Review the memory panel and delete anything you do not want retained. Disable Persistent memory in Settings to stop new memory extraction; existing memories remain until deleted.

## Web-search and fetch behavior

- `web_search` queries independent `ddgs` providers concurrently, deduplicates results, then fetches the returned result pages live in parallel. It is available in normal Chat mode; users do not need to switch to Research. `parallel_search` performs discovery only, and `get_results` fetches up to eight selected URLs concurrently. Search provider failures are surfaced as failed tool events rather than represented as empty research.
- `fetch_data` fetches public HTML and plain-text pages, extracts readable text, and returns the source URL/title/time to the model.
- Pages that disallow retrieval through `robots.txt` are blocked. If the robots policy cannot be checked, retrieval fails closed, except when the server explicitly returns HTTP 404 for `robots.txt`.
- This is not a whole-internet crawler. It does not bypass authentication, paywalls, CAPTCHAs, rate limits, robots rules, or JavaScript-only access controls. PDFs and binary documents are not yet extracted by this fetcher. Research activity records actual search queries/results, page fetches, and calculations. Evidence snapshots and numerical experiment inputs/outputs persist. Job execution itself still depends on an open server process and is not resumable after a server restart.
- Search snippets are discovery hints; ClaudeX should fetch important pages and cite sources actually used.

## Evidence and experiments

Open a conversation to see its evidence ledger. A result marked `discovered` has only a search snippet; `inspected` means ClaudeX fetched and stored the page text. Click an inspected entry to view the saved snapshot. Source IDs are provided to final synthesis for near-claim citations, scoped to the current turn. This records provenance but does not yet perform automated passage-to-claim entailment verification.

The optional numerical experiment tool is deliberately narrow: it compares two formulas involving `x`, arithmetic, and whitelisted elementary functions on at most 101 points. It reports numerical agreement on that grid only; it does not establish a theorem or physical result.

## Mathematics

The `symbolic_math` tool uses SymPy. Supported operations are simplify, factor, expand, differentiate, integrate, solve, and limit. It uses a restricted parser; arbitrary Python expressions are not evaluated. The assistant is prompted to show derivations in LaTeX and explain assumptions.

## Data locations

- `.env` — local NVIDIA key and preferences; chmod `0600`.
- `data/claudex.sqlite3` — conversations, messages, memories.
- `static/` — local web interface assets.

Back up `data/claudex.sqlite3` to preserve chats and memories. Back up `.env` separately and keep it secret.

## Troubleshooting

**Invalid API key / 401:** verify the key in Settings and confirm it is an NVIDIA Build API key.

**Model not found / 404:** the endpoint may not be enabled for the key, or the selected model ID may have changed. Try Nemotron 3.5 Lightning and check the current NVIDIA model catalog.

**429 / rate limit:** wait, review NVIDIA endpoint limits/usage, or select an endpoint with available quota.

**No web results:** search provider availability, connectivity, and package version can affect DDGS. Update dependencies with `pip install -U -r requirements.txt`.

**LaTeX or tables do not render:** MathJax, Marked and DOMPurify are loaded from a CDN, so the browser needs internet access for those frontend libraries. Plain Markdown remains readable if the CDN is unavailable.

## Validation

Install the development dependencies and run the backend/API and control mapping tests:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
node --check static/app.js
```

The chat inference test uses a deterministic fake NVIDIA client, so it checks request construction and streaming without an API key or a paid external inference call. Use Settings → Test connection to validate a real key and endpoint.

## API references

- [NVIDIA NIM LLM API reference](https://docs.api.nvidia.com/nim/reference/llm-apis)
- [NVIDIA Build model catalog](https://build.nvidia.com/models?q=nemotron)
- [NVIDIA Nemotron models](https://developer.nvidia.com/nemotron)
