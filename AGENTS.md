# AGENTS.md — Agent Development Guide

## Project: YouTube Ask Proxy API

This document provides context for AI coding agents working on this codebase.

---

## Build & Run

### Install dependencies
```bash
pip install -r requirements.txt
playwright install chromium
```

### Run tests
```bash
pytest
```

### Start development server
```bash
python -m youtube_ask_proxy serve --port 8000
# or
uvicorn youtube_ask_proxy.api:app --reload
```

### Authenticate (required before using)
```bash
python -m youtube_ask_proxy auth
```

---

## Architecture

### Module Responsibilities

| Module | Purpose |
|--------|---------|
| `config/` | Pydantic Settings — loads from env/`.env` |
| `logging/` | Structured logging via `structlog` |
| `models/` | OpenAI-compatible Pydantic request/response types |
| `api/` | FastAPI app, endpoints, error handlers |
| `browser/` | Playwright lifecycle + YouTube Ask DOM interaction (PRIMARY) |
| `gemini/` | Google GenAI SDK client for video summarization (FALLBACK) |
| `auth/` | Cookie/session persistence for Google auth |
| `prompts/` | Build prompts from OpenAI chat messages, extract video URLs |
| `parsers/` | Parse raw DOM text -> structured JSON (markdown strip, JSON repair) |
| `stealth.py` | Anti-detection patches (Chromium args + JS init script) |
| `utils/` | Retry decorators, humanized delays, DOM helpers, string cleanup |
| `main.py` | CLI entry point (`serve`, `auth` commands) |

### Key Design Patterns

1. **Context-manager browser controller** — `BrowserController` supports `async with`
2. **Resilient DOM selectors** — Multiple selector strategies with fallbacks; never rely on a single brittle XPath
3. **Dynamic rendering polling** — `_poll_for_locator()` loops continuously until elements appear or timeout
4. **Retry with exponential backoff** — `utils.with_retry()` decorator via `tenacity`
5. **Global settings singleton** — `config.settings` loaded once at import
6. **Structured logging** — Every module uses `get_logger(__name__)` for JSON/console logs

---

## Testing Strategy

- **Unit tests** for `parsers`, `prompts`, `utils`, and `api` request/response shapes
- **No live browser tests in CI** — Browser automation requires authenticated YouTube session
- Use `TestClient` from FastAPI for endpoint testing

---

## Code Style

- Python 3.10+ with `from __future__ import annotations`
- Type hints on all function signatures
- `ruff` for linting/formatting
- `mypy` for type checking
- Max line length: 100

---

## Environment Variables

See `config/settings.py` for the full schema. Key variables:

- `USER_DATA_DIR` — Persistent Chromium profile (essential for auth)
- `API_KEY` — Optional Bearer token
- `CAPTURE_SCREENSHOTS` — Debug artifact generation
- `HEADLESS` — Set to `false` for visible browser debugging
- `STEALTH_ENABLED` — Anti-detection patches (default `true`)
- `PROMPT_TEMPLATE` — Custom prompt template override

---

## Important Notes for Agents

1. **Never hardcode sleeps** — Use `utils.humanized_delay()` or Playwright explicit waits
2. **Never trust raw DOM output** — Always route through `ResponseParser`
3. **Preserve modularity** — API logic must not directly call Playwright APIs; go through `BrowserController`
4. **Update this file** if you change build steps, module structure, or env var schema
5. **Playwright is preferred** — Do not introduce Selenium unless explicitly requested
6. **Stealth is critical for auth** — If modifying `browser/` or `stealth.py`, test against Google sign-in
7. **Dynamic rendering** — YouTube injects Ask UI after page load; always use `_poll_for_locator()` instead of single-shot selectors
8. **Response container disambiguation** — YouTube injects welcome messages, chips, and the actual AI response with similar classes. The real response has `data-target-id^="youchat-"` and `ytwYouChatItemViewModelColumnLayout`. Never use standalone `markdown-div` selectors.
9. **Coordinate-based clicks** — YouTube's shadow DOM blocks synthetic events. `_human_click()` uses `page.mouse.move()` + `down/up` as the primary strategy.
10. **Fast typing** — For long prompts (>3000 chars), use `fill()` + manual event dispatch instead of `press_sequentially()` to avoid timeout/truncation.
11. **Auth exception handling** — Never use bare `except Exception: continue` in loops that check auth state; it silently swallows `AuthenticationRequiredError`.

---

## Engine Architecture

**PRIMARY:** Playwright / YouTube Ask (~15s avg, 80% success rate)
**FALLBACK:** Gemini API (`google-genai`, ~155s avg, 40% success rate)

The API tries Playwright first. If it fails (Ask button not found, timeout, auth issue), it automatically falls back to Gemini. If both fail, it returns a graceful "unavailable" JSON response. See `COMPARISON.md` for benchmark data.

## Known Limitations

- YouTube Ask feature availability varies by video, region, and account
- DOM selectors may need updates when YouTube changes their UI
- Streaming is simulated (single chunk) because YouTube Ask does not natively stream
- Google's bot detection can still block auth on some IPs/fingerprints despite stealth patches
- Gemini API may fail on long videos (> ~1 hour) due to frame-extraction limits
