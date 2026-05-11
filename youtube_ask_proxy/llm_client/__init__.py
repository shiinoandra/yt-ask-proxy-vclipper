"""Text-based LLM client for auxiliary summarization.

Supports both Gemini API and any OpenAI-compatible API endpoint.
This module is used as the final fallback when Playwright and Gemini
video summarization both fail.
"""

from __future__ import annotations

import json
from typing import Any

from youtube_ask_proxy.config import settings
from youtube_ask_proxy.logging import get_logger
from youtube_ask_proxy.parsers import ResponseParser

logger = get_logger(__name__)


class TextLLMError(Exception):
    """Base exception for text LLM failures."""

    pass


class TextLLMNotConfiguredError(TextLLMError):
    """Raised when no LLM provider is configured."""

    pass


class TextLLMGenerationError(TextLLMError):
    """Raised when the LLM call fails."""

    pass


def _build_text_prompt(base_prompt: str, auxiliary_text: str) -> str:

    base_prompt = """\
Role: Expert VTuber/Gaming Content Analyst.
Task: Analyze the provided closed caption, live chat, and top comments. using the hint and timestamp
information youc ould find, recreate and imagine the
content of the video only with these text information and create a structured JSON report for clip extraction and indexing.

Clip Criteria: Extract moments (max 5m) featuring funny reactions, panic/screams, fails, trolling, high chemistry, or chaotic interactions. Ignore filler.

JSON Schema:

{
  "summary": {
    "main_topics": ["topic1", "topic2"],
    "overall_summary": "Detailed summary of atmosphere, progression, and streamer energy."
  },
  "moments": [
    {
      "time_begin": "HH:MM:SS",
      "time_end": "HH:MM:SS",
      "title": "Short catchy title",
      "category": "funny|hype|fail|chaotic|etc",
      "hype_score": 1-10,
      "desc": "Detailed description of the event.",
      "why_it_is_interesting": "Explanation of entertainment value.",
      "clip_context": "Setup/background for the clip."
    }
  ]
}

Constraint Checklist (CRITICAL):

- Language: Handle JP, EN, and internet slang accurately.
- Timestamps: Must be HH:MM:SS and chronological.
- Format: Output ONLY the raw JSON object.
- No Prose: Do not include markdown code fences (```json), intro text, or outro explanations.
- Max Duration: 5 minutes per clip.
- Min number of clip/moments : 5


"""
    """Build a prompt that instructs the LLM to analyze text-only data.

    The base prompt (e.g. the VTuber analysis template) is preserved,
    but we prepend a note explaining that only text data is available.
    """
    text_context = f"""\
NOTE: Video playback is not available. The following text data has been
extracted from the video (captions, live chat, and/or comments).
Use ONLY this text data to perform the analysis.

--- EXTRACTED TEXT DATA ---

{auxiliary_text}

--- END OF EXTRACTED DATA ---

Now, based SOLELY on the text data above, please perform the analysis.
"""
    # Combine: base prompt first (schema + constraints), then text context
    return f"{base_prompt.strip()}\n\n{text_context}"


async def _call_gemini_text(prompt: str) -> dict[str, Any]:
    """Call Gemini API with a text-only prompt."""
    from google import genai
    from google.genai import types

    if not settings.gemini_api_key:
        raise TextLLMNotConfiguredError("GEMINI_API_KEY not set")

    client = genai.Client(api_key=settings.gemini_api_key)

    config = types.GenerateContentConfig(
        temperature=settings.gemini_temperature,
        top_p=settings.gemini_top_p,
        max_output_tokens=settings.gemini_max_output_tokens,
        response_modalities=["TEXT"],
    )

    logger.info(
        "Calling Gemini API (text-only)",
        model=settings.gemini_model,
        prompt_length=len(prompt),
    )

    try:
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=[types.Part.from_text(text=prompt)],
            config=config,
        )
    except Exception as exc:
        logger.error("Gemini text call failed", error=str(exc))
        raise TextLLMGenerationError(f"Gemini text call failed: {exc}") from exc

    if not response.text:
        raise TextLLMGenerationError("Gemini returned empty response")

    logger.info(
        "Gemini text response received",
        response_length=len(response.text),
        preview=response.text[:200],
    )

    parser = ResponseParser()
    try:
        return parser.parse(response.text)
    except Exception as exc:
        logger.error("Failed to parse Gemini text response", error=str(exc))
        raise TextLLMGenerationError(f"Response parsing failed: {exc}") from exc


async def _call_openai_compatible_text(prompt: str) -> dict[str, Any]:
    """Call an OpenAI-compatible API with a text-only prompt.

    Requires ``OPENAI_BASE_URL``, ``OPENAI_API_KEY``, and ``OPENAI_MODEL``
    to be configured in settings.
    """
    if not settings.openai_base_url or not settings.openai_api_key:
        raise TextLLMNotConfiguredError(
            "OpenAI-compatible API not configured. "
            "Set OPENAI_BASE_URL, OPENAI_API_KEY, and OPENAI_MODEL."
        )

    import httpx

    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": settings.openai_model,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": prompt},
        ],
        "temperature": settings.openai_temperature,
        "max_tokens": settings.openai_max_tokens,
    }

    logger.info(
        "Calling OpenAI-compatible API (text-only)",
        base_url=settings.openai_base_url,
        model=settings.openai_model,
        prompt_length=len(prompt),
    )

    try:
        async with httpx.AsyncClient(timeout=settings.openai_timeout) as client:
            resp = await client.post(
                f"{settings.openai_base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.error("OpenAI-compatible text call failed", error=str(exc))
        raise TextLLMGenerationError(f"OpenAI-compatible call failed: {exc}") from exc

    content = data["choices"][0]["message"]["content"]
    if not content:
        raise TextLLMGenerationError("OpenAI-compatible API returned empty content")

    logger.info(
        "OpenAI-compatible response received",
        response_length=len(content),
        preview=content[:200],
    )

    parser = ResponseParser()
    try:
        return parser.parse(content)
    except Exception as exc:
        logger.error("Failed to parse OpenAI-compatible response", error=str(exc))
        raise TextLLMGenerationError(f"Response parsing failed: {exc}") from exc


async def summarize_with_text_llm(
    base_prompt: str,
    auxiliary_text: str,
) -> dict[str, Any]:
    """Summarize video using only text data via an LLM.

    Tries providers in order:
        1. OpenAI-compatible API (default: local server at localhost:7860)
        2. Gemini API (fallback)

    Override via environment variables:
        - OPENAI_BASE_URL (default: http://localhost:7860/v1)
        - OPENAI_API_KEY  (default: not-needed)
        - OPENAI_MODEL    (default: local-model)
        - GEMINI_API_KEY  (fallback, only used if local server fails)

    Args:
        base_prompt: The original prompt template (e.g. VTuber analysis).
        auxiliary_text: Extracted text data (captions, comments, chat).

    Returns:
        Parsed JSON dict with summary and moments.

    Raises:
        TextLLMNotConfiguredError: If no LLM provider is available.
        TextLLMGenerationError: If all configured providers fail.
    """
    prompt = _build_text_prompt(base_prompt, auxiliary_text)

    # Strategy 1: OpenAI-compatible API (local server by default)
    if settings.openai_base_url:
        try:
            return await _call_openai_compatible_text(prompt)
        except TextLLMGenerationError:
            logger.warning("OpenAI-compatible text summarization failed, trying Gemini")
        except Exception as exc:
            logger.warning("Unexpected OpenAI-compatible error", error=str(exc))

    # Strategy 2: Gemini API (fallback)
    if settings.gemini_api_key:
        try:
            return await _call_gemini_text(prompt)
        except TextLLMGenerationError:
            logger.warning("Gemini text summarization failed")
        except Exception as exc:
            logger.warning("Unexpected Gemini text error", error=str(exc))

    raise TextLLMNotConfiguredError(
        "No text LLM provider succeeded. "
        "Check that your local server is running at OPENAI_BASE_URL, "
        "or configure GEMINI_API_KEY as a fallback."
    )
