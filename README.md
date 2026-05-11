# YouTube Ask Proxy API

> **OpenAI-compatible REST API for YouTube video summarization powered by YouTube Ask browser automation (primary) and Gemini API (fallback).**

---

## Table of Contents

1. [Objective](#objective)
2. [Technology Stack](#technology-stack)
3. [Architecture Overview](#architecture-overview)
4. [Project Structure](#project-structure)
5. [Component Workflows](#component-workflows)
6. [Getting Started](#getting-started)
7. [Configuration](#configuration)
8. [API Documentation](#api-documentation)
9. [Example Requests](#example-requests)
10. [Response Format](#response-format)
11. [Authentication & Stealth](#authentication--stealth)
12. [Troubleshooting](#troubleshooting)
13. [Development](#development)
14. [License](#license)

---

## Objective

YouTube's **Ask** feature is a powerful AI-driven video Q&A tool, but Google does not expose it through any official public API. It is only available inside the browser, gated behind Google authentication, and rendered dynamically via JavaScript.

This project bridges that gap with a **dual-engine architecture**:

### Primary: YouTube Ask via Playwright
The fastest and most reliable method. Uses **Playwright** to drive a real Chromium instance, navigate to YouTube, open the Ask panel, submit the prompt, and extract the AI response from the DOM. YouTube's Ask feature leverages Google's pre-built video index (captions, chapters, engagement signals), so results typically return in **10–20 seconds** with high accuracy.

### Fallback: Gemini API
When YouTube Ask is unavailable (not enabled for the video, auth expired, etc.), the API transparently falls back to **Google's Gemini API** (`google-genai` SDK). Gemini processes the video directly via `Part.from_uri()`, which is slower (~20–300s) and may fail on very long videos, but covers videos where Ask is not enabled.

### Key Benefits
1. **Speed** — YouTube Ask averages **15.8s** vs Gemini's **154.7s** (10× faster).
2. **Reliability** — Playwright succeeds on **80%** of videos vs Gemini's **40%**; combined they cover **100%**.
3. **Graceful degradation** — If Playwright fails (Ask not enabled), Gemini tries. If both fail, a clean "unavailable" message is returned.
4. **OpenAI-compatible REST API** — Any client that speaks `/v1/chat/completions` can use it without code changes.

> See [COMPARISON.md](COMPARISON.md) for the full benchmark data and methodology.

---

## Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Language** | Python 3.10+ | Core runtime |
| **Web Framework** | FastAPI | OpenAI-compatible HTTP API |
| **Server** | Uvicorn | ASGI server with WebSocket support |
| **Primary LLM** | Playwright (async) | Browser automation for YouTube Ask |
| **Fallback LLM** | Gemini API (`google-genai`) | Native video summarization via `Part.from_uri` |
| **Data Validation** | Pydantic v2 | Request/response models, settings |
| **Settings** | Pydantic-Settings | `.env`/environment configuration |
| **Logging** | structlog + python-json-logger | Structured JSON or console logs |
| **Resilience** | tenacity | Exponential-backoff retry decorator |
| **JSON Repair** | Custom (built-in) | Strip markdown, fix trailing commas, balance braces |
| **Type Checking** | mypy | Static analysis |
| **Linting** | ruff | Formatting & linting |
| **Testing** | pytest, pytest-asyncio | Unit tests |

**Browser:** Chromium (via Playwright). Always pre-launched at startup since it's the primary engine.

---

## Architecture Overview

```
+-------------------------+
|   Client Application    |
| (curl, openai SDK, etc) |
+-----------+-------------+
            | HTTP / OpenAI protocol
            v
+-------------------------+
|  FastAPI REST API       |
|  (/v1/chat/completions) |
+-----------+-------------+
            |
            v
+-------------------------+
|   Prompt Builder        |
|   - Extract video URL   |
|   - Assemble prompt     |
+-----------+-------------+
            |
      +-----+-----+
      |           |
      v           v
+-------------------------+     +-------------------------+
|  Browser Controller     |     |   Gemini API Client     |
|  (Playwright -> Chrome) |     |  (google-genai SDK)     |
|  - Navigate to video    |     |  - Part.from_uri(video) |
|  - Poll for Ask UI      |     |  - Send prompt          |
|  - Submit & extract     |     |  - Receive JSON         |
+-----------+-------------+     +-----------+-------------+
            |                               |
            |         (fallback)            |
            +---------->+<------------------+
                        |
                        v
+-------------------------+
|   Response Parser       |
|   - Strip markdown      |
|   - Repair JSON         |
|   - Validate structure  |
+-----------+-------------+
            |
            v
+-------------------------+
|  OpenAI-format JSON     |
+-------------------------+
```

---

## Project Structure

```
youtube_ask_proxy/
├── youtube_ask_proxy/
│   ├── __init__.py           # Package version
│   ├── __main__.py           # python -m youtube_ask_proxy entry point
│   ├── main.py               # CLI (serve, auth commands)
│   ├── api/                  # FastAPI app, endpoints, error handlers
│   ├── auth/                 # Cookie/session persistence
│   ├── browser/              # Playwright lifecycle + YouTube Ask DOM interaction
│   ├── config/               # Pydantic Settings (env / .env)
│   ├── gemini/               # Gemini API client (google-genai SDK)
│   ├── logging/              # Structured logging setup
│   ├── models/               # OpenAI-compatible Pydantic types
│   ├── parsers/              # Response parsing & JSON repair
│   ├── prompts/              # Prompt construction + URL extraction
│   ├── stealth.py            # Anti-detection patches
│   └── utils/                # Retry decorators, DOM helpers, string utils
├── tests/                    # pytest suite
├── requirements.txt
├── pyproject.toml
├── .gitignore
├── README.md                 # This file
└── AGENTS.md                 # Agent/coding-bot context
```

---

## Component Workflows

### API Layer (`api/`)

FastAPI application with two lifespan phases:

1. **Startup** — Configures structured logging, optionally pre-launches the browser controller to fail fast on missing Playwright binaries.
2. **Shutdown** — Gracefully stops the browser and saves cookies.

**Endpoints:**
- `GET /v1/models` — Lists available models.
- `POST /v1/chat/completions` — Main chat completion endpoint.

**Error Handling:** Every exception is converted to an OpenAI-compatible error envelope:

```json
{
  "error": {
    "message": "Human readable description",
    "type": "browser_automation_error",
    "param": null,
    "code": "browser_error"
  }
}
```

### Prompt Builder (`prompts/`)

**Flow:**
1. Accept OpenAI-style chat messages (`system`, `user`, `assistant`).
2. Extract any YouTube URL from user messages using regex (`youtube.com/watch`, `youtu.be`, `youtube.com/shorts`).
3. Build the final prompt string:
   - If `PROMPT_TEMPLATE` is set -> use the custom template with `{system}` and `{user}` placeholders.
   - Otherwise -> use the built-in **VTuber/livestream analysis template**.
4. Return `(prompt_text, video_url)`.

**Default Template (VTuber Analysis):**
The built-in template instructs the model to return structured JSON containing:
- `summary.main_topics` — list of topics
- `summary.overall_summary` — detailed narrative summary
- `moments[]` — array of clip-worthy moments with timestamps, titles, hype scores, descriptions

This template is optimised for Japanese/English VTuber, gaming, and collaboration content.

### Browser Controller (`browser/`)

**Flow for a single `ask(video_url, prompt)` call:**

```
1. navigate_to_video(video_url)
   |- page.goto(wait_until="domcontentloaded")
   |- page.wait_for_selector("ytd-app")
   |- wait_for_load_state("networkidle") OR sleep(page_settle_timeout)
   |- Check for "Sign in" indicators -> raise AuthenticationRequiredError

2. _wait_for_ask_button(page)
   |- POLL loop (every 500ms, up to 20s)
   |- Try multiple selectors until one matches

3. _human_click(ask_button)
   |- Strategy 1: Coordinate-based mouse click (primary — bypasses shadow DOM)
   |- Strategy 2: Playwright force click
   |- Strategy 3: Click deepest interactive child
   |- Strategy 4: JS el.click()
   |- Strategy 5: PointerEvent dispatch

4. _wait_for_ask_input(page)
   |- POLL loop (every 500ms, up to 15s)

5. _human_type(ask_input, prompt)
   |- locator.fill() for speed (avoids 60s+ typing on 3000-char prompts)
   |- Fire focus/keydown/input/keyup/change events for framework listeners

6. _wait_for_ask_submit(page)  OR  press Enter as fallback

7. _wait_for_response_text(page)
   |- PHASE 1: Poll for response container (targets data-target-id^="youchat-")
   |- PHASE 2: Poll until:
        a) Thumbs up/down buttons appear (strong completion signal), OR
        b) Text stabilises for 3 consecutive polls
   |- Return text (or partial text on timeout)
```

**Anti-Bot Interaction:** YouTube's Web Components and shadow DOM block synthetic DOM events. The primary click strategy moves the **real OS mouse** to the element's screen coordinates, bypassing shadow-DOM boundaries entirely.

**Retries:** The entire `ask()` method is wrapped with `tenacity` (exponential backoff, up to `MAX_RETRIES`).

### Stealth / Anti-Detection (`stealth.py`)

Playwright is not inherently stealthy. Google detects it via:
- `--enable-automation` Chromium flag
- `navigator.webdriver === true`
- Missing `window.chrome.runtime`
- Empty `navigator.plugins`
- Blink `AutomationControlled` feature

**Mitigations applied automatically when `STEALTH_ENABLED=true` (default):**

1. **Launch args** passed to Chromium:
   - `--disable-blink-features=AutomationControlled`
   - `--disable-infobars`
   - `--disable-background-networking`
   - `--no-first-run`, `--disable-sync`, etc.

2. **Runtime init script** injected before any page JS executes:
   - Deletes `navigator.webdriver`
   - Fakes `window.chrome.runtime`
   - Populates `navigator.plugins` with PDF + Native Client entries
   - Sets `navigator.languages`, `deviceMemory`, `hardwareConcurrency`
   - Patches `Permissions.prototype.query`

### Authentication Manager (`auth/`)

**Flow:**
1. `USER_DATA_DIR` -> persistent Chromium profile directory. If set, Playwright launches a **persistent context** so cookies, localStorage, and IndexedDB survive restarts.
2. `COOKIES_FILE` -> optional JSON file for additional cookie backup/restore.
3. On shutdown, cookies are extracted from the context and saved to `COOKIES_FILE` (unless a persistent profile is already being used).

**Why persistent profiles matter:** YouTube/Google aggressively expire sessions if they see new browser fingerprints. A persistent profile keeps the same fingerprint across runs, drastically reducing re-auth frequency.

### Response Parser (`parsers/`)

**Strategies (tried in order):**

1. **Strip markdown fences** — Remove ` ```json ... ``` ` wrappers.
2. **Direct JSON parse** — `json.loads()` on the cleaned text.
3. **Extract JSON objects** — Scan for `{` ... `}` pairs using `json.JSONDecoder.raw_decode()`.
4. **Repair malformed JSON** — Fix trailing commas, add missing closing braces/brackets, convert single quotes to double quotes.
5. **Fallback wrapper** — If nothing works, wrap the raw text in `{"response": "...", "_parsed": false}`.

---

## Getting Started

### Prerequisites

- Python 3.10+
- Linux/macOS/Windows
- A Google account with access to YouTube Ask

### 1. Install Dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Authenticate with YouTube

```bash
python -m youtube_ask_proxy auth
```

A visible browser window opens. Sign in to your Google account, complete any 2FA, and wait until YouTube loads. Then close the browser or press `Ctrl+C`.

By default the profile is saved to `./browser_data/`.

> **Tip:** If you see "This browser or app may not be secure", make sure `STEALTH_ENABLED` is `true` (default) and try again. Using a copied real Chrome profile also helps.

### 3. Start the API Server

```bash
python -m youtube_ask_proxy serve
```

Or with Uvicorn directly:

```bash
uvicorn youtube_ask_proxy.api:app --host 0.0.0.0 --port 8000 --reload
```

### 4. Make Your First Request

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "youtube-ask-proxy",
    "messages": [
      {
        "role": "user",
        "content": "Analyze this stream: https://www.youtube.com/watch?v=VIDEO_ID"
      }
    ]
  }'
```

---

## Configuration

All settings are loaded from environment variables or a `.env` file in the project root.

### Server Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `API_HOST` | `0.0.0.0` | Bind host |
| `API_PORT` | `8000` | Bind port |
| `API_WORKERS` | `1` | Uvicorn worker processes |
| `API_KEY` | `None` | Optional Bearer token. If set, every request must include `Authorization: Bearer <token>` |

### Browser Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `BROWSER_TYPE` | `chromium` | Playwright browser type |
| `HEADLESS` | `true` | Run without GUI window |
| `SLOW_MO` | `50` | Artificial delay between Playwright actions (ms) |
| `BROWSER_TIMEOUT` | `30000` | Generic Playwright operation timeout (ms) |
| `NAVIGATION_TIMEOUT` | `60000` | Page load timeout (ms) |
| `RESPONSE_TIMEOUT` | `120000` | Max time to wait for AI answer (ms) |
| `PAGE_SETTLE_TIMEOUT` | `10000` | Time to wait for YouTube JS to finish injecting components (ms) |
| `ASK_FEATURE_DETECTION_TIMEOUT` | `20000` | Max polling time for the Ask button to appear (ms) |
| `ASK_PANEL_OPEN_TIMEOUT` | `15000` | Max polling time for the Ask panel to open after click (ms) |
| `ASK_POLL_INTERVAL` | `0.5` | Seconds between DOM polling attempts |

### Authentication Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `USER_DATA_DIR` | `./browser_data` | Persistent Chromium profile path **(essential for auth)** |
| `COOKIES_FILE` | `None` | Path to a JSON file for cookie backup |
| `AUTH_REQUIRED` | `true` | Whether to check for signed-in state |

### Stealth Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `STEALTH_ENABLED` | `true` | Enable anti-detection patches |
| `USER_AGENT` | `None` | Override default User-Agent |
| `VIEWPORT_WIDTH` | `1920` | Browser viewport width |
| `VIEWPORT_HEIGHT` | `1080` | Browser viewport height |
| `LOCALE` | `en-US` | Browser locale |
| `TIMEZONE` | `America/New_York` | Browser timezone |

### Reliability Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_RETRIES` | `3` | Retry attempts for browser failures |
| `RETRY_BASE_DELAY` | `1.0` | Initial retry backoff (seconds) |
| `RETRY_MAX_DELAY` | `30.0` | Max retry backoff (seconds) |

### Observability Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `LOG_FORMAT` | `console` | `console` or `json` |
| `CAPTURE_SCREENSHOTS` | `false` | Save PNG + HTML dumps on failures |
| `SCREENSHOT_DIR` | `screenshots` | Directory for debug artifacts |
| `ENABLE_TRACING` | `false` | Enable Playwright trace files |

### Gemini API Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | `None` | **Google Gemini API key** (get one at [aistudio.google.com](https://aistudio.google.com)). When set, Gemini becomes the primary summarization engine. |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Model name (e.g., `gemini-2.5-flash`, `gemini-3.1-flash-lite-preview`) |
| `GEMINI_TEMPERATURE` | `1.0` | Sampling temperature (0.0–2.0) |
| `GEMINI_TOP_P` | `0.95` | Nucleus sampling (0.0–1.0) |
| `GEMINI_MAX_OUTPUT_TOKENS` | `8192` | Max tokens in response |
| `GEMINI_TIMEOUT` | `120000` | API call timeout in milliseconds |
| `GEMINI_ENABLED` | `true` | Whether to use Gemini API (set `false` to force Playwright only) |

### Prompt Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `PROMPT_TEMPLATE` | `None` | Custom prompt template string. Uses `{system}` and `{user}` placeholders. If unset, the built-in VTuber analysis template is used. |

### Example `.env`

```env
# Server
API_KEY=sk-youtube-proxy-secret

# Primary: Gemini API (recommended)
GEMINI_API_KEY=your-gemini-api-key-here
GEMINI_MODEL=gemini-2.0-flash
GEMINI_ENABLED=true

# Fallback: Playwright / YouTube Ask
USER_DATA_DIR=./browser_data
STEALTH_ENABLED=true
HEADLESS=true

# Debugging
CAPTURE_SCREENSHOTS=true
LOG_LEVEL=DEBUG
LOG_FORMAT=console

# Prompt override (optional)
PROMPT_TEMPLATE="Analyze the video and return JSON. Context: {system} Question: {user}"
```

---

## API Documentation

### Authentication

If `API_KEY` is configured, include it in every request:

```bash
-H "Authorization: Bearer sk-youtube-proxy-secret"
```

If `API_KEY` is not set, no authentication is required.

---

### `GET /v1/models`

List available models.

**Response (200):**

```json
{
  "object": "list",
  "data": [
    {
      "id": "youtube-ask-proxy",
      "object": "model",
      "created": 0,
      "owned_by": "youtube-ask-proxy"
    }
  ]
}
```

---

### `POST /v1/chat/completions`

Create a chat completion. Compatible with the OpenAI Chat Completions API.

**Headers:**
- `Content-Type: application/json`
- `Authorization: Bearer <API_KEY>` (if configured)

**Request Body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | `string` | No | Ignored. Always treated as `youtube-ask-proxy`. |
| `messages` | `array` | **Yes** | OpenAI-format messages. At least one `user` message is required. |
| `stream` | `boolean` | No | If `true`, returns a streaming SSE response. Default: `false`. |
| `video_url` | `string` | No | Explicit YouTube URL. If omitted, the system attempts to extract one from `messages`. |
| `temperature` | `float` | No | Ignored (YouTube Ask does not expose this). |
| `max_tokens` | `integer` | No | Ignored. |
| `top_p` | `float` | No | Ignored. |
| `n` | `integer` | No | Ignored. Always `1`. |

**`messages` item schema:**

```json
{
  "role": "user",
  "content": "Your text",
  "name": null
}
```

**Error Responses:**

| Status | Condition |
|--------|-----------|
| `400` | No `video_url` provided and none found in messages. |
| `401` | Invalid or missing `API_KEY`. |
| `502` | Browser automation failure or Gemini API error. |
| `503` | Both Gemini and Playwright failed — summarization unavailable for this video. |
| `500` | Unexpected internal error. |

**Graceful Degradation:**
When both methods fail, the API returns HTTP `200` with a structured JSON payload indicating the error, so clients can handle it gracefully:

```json
{
  "summary": {
    "main_topics": [],
    "overall_summary": "Summarization is not available for this video."
  },
  "moments": [],
  "error": true,
  "message": "Summarization is not available for this video.",
  "details": "The video may not be accessible, the Gemini API key is invalid, the Ask feature is not enabled, or the service is temporarily unavailable."
}
```

---

## Example Requests

### Non-Streaming Request

```bash
curl -s http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-youtube-proxy-secret" \
  -d '{
    "model": "youtube-ask-proxy",
    "messages": [
      {
        "role": "user",
        "content": "Analyze this VTuber stream: https://www.youtube.com/watch?v=dQw4w9WgXcQ"
      }
    ]
  }' | jq .
```

**Expected Response:**

```json
{
  "id": "chatcmpl-a1b2c3d4e5f6",
  "object": "chat.completion",
  "created": 1715432100,
  "model": "youtube-ask-proxy",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "{\n  \"summary\": {\n    \"main_topics\": [\"Gaming\", \"Collab\"],\n    \"overall_summary\": \"The streamer...\"\n  },\n  \"moments\": [\n    {\n      \"time_begin\": \"00:05:23\",\n      \"time_end\": \"00:07:45\",\n      \"title\": \"Panic reaction to boss\",\n      \"category\": \"funny\",\n      \"hype_score\": 9,\n      \"desc\": \"...\",\n      \"why_it_is_interesting\": \"...\",\n      \"clip_context\": \"...\"\n    }\n  ]\n}"
      },
      "finish_reason": "stop",
      "logprobs": null
    }
  ],
  "usage": {
    "prompt_tokens": 42,
    "completion_tokens": 256,
    "total_tokens": 298
  },
  "system_fingerprint": null
}
```

> **Note:** `content` is a **JSON string**. Parse it client-side with `JSON.parse(response.choices[0].message.content)` to get the structured object.

---

### Streaming Request

```bash
curl -N http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-youtube-proxy-secret" \
  -d '{
    "model": "youtube-ask-proxy",
    "messages": [
      {"role": "user", "content": "https://youtu.be/dQw4w9WgXcQ Summarize"}
    ],
    "stream": true
  }'
```

**Expected SSE Output:**

```
data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1715432100,"model":"youtube-ask-proxy","choices":[{"index":0,"delta":{"role":"assistant","content":"{ \"summary\": ... }"},"finish_reason":null}]}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1715432100,"model":"youtube-ask-proxy","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":"stop"}]}

data: [DONE]
```

> **Note:** Streaming is **simulated** (single chunk) because neither Gemini nor YouTube Ask natively stream tokens. The full response is returned in one SSE event, followed by a `[DONE]` terminator.

---

### Using the Python `openai` Library

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000",
    api_key="sk-youtube-proxy-secret",
)

response = client.chat.completions.create(
    model="youtube-ask-proxy",
    messages=[
        {
            "role": "user",
            "content": "Extract highlights from https://www.youtube.com/watch?v=VIDEO_ID"
        }
    ]
)

# The content is a JSON string
import json
data = json.loads(response.choices[0].message.content)
print(data["summary"]["overall_summary"])
for moment in data["moments"]:
    print(moment["time_begin"], moment["title"])
```

---

## Response Format

The `content` field returned by the API is **always a JSON string** (never raw DOM text). Its exact schema depends on the prompt template.

### Default Template (VTuber Analysis)

```json
{
  "summary": {
    "main_topics": ["Topic 1", "Topic 2"],
    "overall_summary": "Detailed narrative summary..."
  },
  "moments": [
    {
      "time_begin": "HH:MM:SS",
      "time_end": "HH:MM:SS",
      "title": "Short descriptive title",
      "category": "funny",
      "hype_score": 8,
      "desc": "What happened during the clip",
      "why_it_is_interesting": "Why viewers would share this",
      "clip_context": "What led up to this moment"
    }
  ]
}
```

**Category values:** `funny`, `hype`, `emotional`, `fail`, `clutch`, `collab_chemistry`, `chat_reaction`, `scary`, `chaotic`, `wholesome`, `rage`, `trolling`, `singing`, `unexpected`

**`hype_score`:** Integer `1-10`. `10` = extremely viral/clip-worthy.

---

## Authentication & Stealth

### Why Google Blocks Automation

Google's sign-in page detects:
- `--enable-automation` Chromium flag
- `navigator.webdriver === true`
- Missing `window.chrome.runtime`
- Empty `navigator.plugins`
- Blink `AutomationControlled` feature

### What This Project Does

When `STEALTH_ENABLED=true` (default):

1. **Launch flags** hide the `AutomationControlled` Blink feature.
2. **Runtime JS** patches `navigator.webdriver`, fakes Chrome plugins, and populates plausible device metadata.
3. **Persistent profiles** reuse the same browser fingerprint across sessions.

### Recommendations

1. **Use a persistent profile** (`USER_DATA_DIR=./browser_data`).
2. **Run auth in headed mode** (`HEADLESS=false`) at least once. Google is far more suspicious of headless browsers during sign-in.
3. **If still blocked**, copy your existing Chrome user data directory:
   - Linux: `~/.config/google-chrome/Default` -> point `USER_DATA_DIR` at the parent folder.
   - macOS: `~/Library/Application Support/Google/Chrome/Default`
   - Windows: `%LOCALAPPDATA%\Google\Chrome\User Data\Default`
4. **Avoid VPNs or datacenter IPs** during the initial auth flow.
5. After successful auth, you can switch back to `HEADLESS=true` for the API server.

---

## Troubleshooting

### "Ask feature not found on page"

**Causes:**
- The video does not have Ask enabled (varies by video, region, account).
- YouTube changed the DOM selectors.
- The component hadn't been injected yet (network was too slow).
- The selector matched the wrong button (e.g., CC/subtitles button with `aria-label*="AI"`).

**Fixes:**
- Increase `ASK_FEATURE_DETECTION_TIMEOUT` (e.g., `30000`).
- Enable screenshots (`CAPTURE_SCREENSHOTS=true`) and inspect `./screenshots/ask_not_found_*.png`.
- Check if Ask appears manually in a regular browser for the same video.
- If debugging selectors, use `_inspect_element()` to log tag names, classes, and bounding boxes.

### "Authentication required but not signed in"

**Causes:**
- Session expired.
- Cookies were not saved.
- `USER_DATA_DIR` is not configured.

**Fixes:**
- Re-run `python -m youtube_ask_proxy auth`.
- Ensure `USER_DATA_DIR` points to the same directory used during auth.
- Set `HEADLESS=false` temporarily to see what's happening.

### "Browser might not be secure" during Google sign-in

**Causes:**
- Stealth patches are disabled or insufficient.
- Google has flagged the IP or fingerprint.

**Fixes:**
- Verify `STEALTH_ENABLED=true` (default).
- Use a copied real Chrome profile (see Authentication & Stealth above).
- Try from a residential IP.

### Slow responses / timeouts

**Causes:**
- YouTube Ask is genuinely slow.
- Network instability.
- The response container selector is not matching.

**Fixes:**
- Increase `RESPONSE_TIMEOUT` (e.g., `180000` for 3 minutes).
- Increase `PAGE_SETTLE_TIMEOUT` if the page loads heavy scripts.
- Enable debug logging (`LOG_LEVEL=DEBUG`) to see polling progress.

### "Not sure what to ask?" or chips extracted instead of AI response

**Causes:**
- The response container selector matched a welcome message or follow-up question chips.
- Welcome messages and chips share `class="ytwYouChatItemViewModelHost"` but have different `data-target-id` attributes.

**Fixes:**
- The built-in selectors already require `data-target-id^="youchat-"` and `ColumnLayout`.
- If YouTube changes their DOM again, update `_wait_for_response_container()` to use the new disambiguating attributes.
- Enable `LOG_LEVEL=DEBUG` to see which selector matched and what text was extracted.

### JSON parsing errors in response

**Causes:**
- YouTube Ask returned conversational prose instead of JSON.
- The prompt did not explicitly request JSON.
- The wrong DOM element was captured (e.g., welcome message or chips).

**Fixes:**
- Use the default VTuber analysis template (it explicitly demands JSON).
- If using a custom `PROMPT_TEMPLATE`, ensure it contains strong JSON instructions.
- The parser will fallback to wrapping raw text; check `content` for `_parsed: false`.
- Verify the correct response element was captured by checking the debug logs for `tag`, `class_name`, and `text_preview`.

---

## Development

### Running Tests

```bash
pytest
```

Unit tests cover parsers, prompts, utilities, and API request/response shapes. Browser automation tests are excluded from the default suite because they require a live, authenticated YouTube session.

### Linting & Type Checking

```bash
ruff check .          # lint
ruff format .         # format
mypy youtube_ask_proxy --ignore-missing-imports   # type check
```

### DOM Selector Resilience

YouTube's UI changes frequently. The browser controller uses a **multi-layer selector strategy** to survive DOM changes:

**Ask Button Detection:**
- `button[aria-label="Ask"]` — exact match (avoids matching CC/subtitle buttons)
- `button.ytSpecButtonShapeNextHost:has-text("Ask")`
- `yt-button-view-model:has-text("Ask") button`
- `.you-chat-entrypoint-button button`
- Plus 6 additional fallback selectors

**Response Container Detection (critical):**
YouTube injects multiple chat items with similar classes. The response container must be disambiguated:

1. **Primary selector:** `you-chat-item-view-model.ytwYouChatItemViewModelColumnLayout[data-target-id^="youchat-"]:has(.ytwThumbsUpDownThumbs)`
   - `data-target-id^="youchat-"` — filters out welcome messages (`data-target-id=""`)
   - `ColumnLayout` — distinguishes AI responses from follow-up question chips
   - `:has(.ytwThumbsUpDownThumbs)` — ensures the response is fully rendered

2. **Fallback selectors:** `markdown-div` scoped inside `data-target-id^="youchat-"` parents, plus older generic selectors.

**Completion Detection:**
Instead of waiting an arbitrary time, the code detects response completion via:
- **Thumbs up/down buttons** (`.ytwThumbsUpDownThumbs`) — strongest signal that rendering is done
- **Text stabilization** — fallback if thumbs don't appear

### Adding New DOM Selectors

If the Ask feature moves to a new component:

1. Open `youtube_ask_proxy/browser/__init__.py`.
2. Find `_wait_for_ask_button()`, `_wait_for_ask_input()`, `_wait_for_ask_submit()`, or `_wait_for_response_container()`.
3. **Prepend** new selectors at the top of the list (highest priority).
4. Keep old selectors as fallbacks — never rely on a single XPath or class name.
5. If adding response selectors, ensure they scope to `data-target-id^="youchat-"` to avoid matching welcome messages or chips.

### Custom Prompts

Set `PROMPT_TEMPLATE` to override the default VTuber analysis template:

```env
PROMPT_TEMPLATE="{system}\n\nAnalyze the video and return JSON with a 'summary' field.\nUser request: {user}"
```

The placeholders `{system}` and `{user}` are replaced at runtime.

---

## License

MIT
