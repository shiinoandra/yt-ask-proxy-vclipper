"""Gemini API client for YouTube video summarization.

Uses the ``google-genai`` SDK to send YouTube video URLs directly to Gemini
as video parts, avoiding the need for browser automation.

Reference: https://codelabs.developers.google.com/devsite/codelabs/build-youtube-summarizer
"""

from __future__ import annotations

import json
from typing import Any

from youtube_ask_proxy.config import settings
from youtube_ask_proxy.logging import get_logger
from youtube_ask_proxy.parsers import ResponseParser

logger = get_logger(__name__)


class GeminiAPIError(Exception):
    """Raised when the Gemini API call fails."""

    pass


class GeminiNotConfiguredError(GeminiAPIError):
    """Raised when Gemini API key is not configured."""

    pass


class GeminiSummarizationError(GeminiAPIError):
    """Raised when Gemini fails to summarize a video."""

    pass


def _get_client() -> Any:
    """Lazy-load the Gemini client.

    Returns:
        Configured ``genai.Client`` instance.

    Raises:
        GeminiNotConfiguredError: If ``GEMINI_API_KEY`` is not set.
    """
    from google import genai

    if not settings.gemini_api_key:
        raise GeminiNotConfiguredError(
            "Gemini API key not configured. Set GEMINI_API_KEY environment variable."
        )
    return genai.Client(api_key=settings.gemini_api_key)


def _build_gemini_prompt(prompt_text: str) -> str:
    """Build the final prompt for Gemini.

    Gemini receives the video as a separate part, so the prompt only needs
    to contain the instructions / question.

    Args:
        prompt_text: The prompt text (already built by PromptBuilder).

    Returns:
        Cleaned prompt string suitable for Gemini.
    """
    return prompt_text.strip()


async def summarize_video(video_url: str, prompt_text: str) -> dict[str, Any]:
    """Summarize a YouTube video using the Gemini API.

    Sends the video URL as a ``Part.from_uri()`` with ``mime_type="video/*"``
    so that Gemini can natively ingest the video content.

    Args:
        video_url: Full YouTube video URL.
        prompt_text: Prompt / instructions for the summarization.

    Returns:
        Parsed JSON dictionary (same schema as the Playwright method).

    Raises:
        GeminiNotConfiguredError: If the API key is missing.
        GeminiSummarizationError: If the API call or JSON parsing fails.
    """
    client = _get_client()
    from google.genai import types

    # Prepare the video part
    video_part = types.Part.from_uri(
        file_uri=video_url,
        mime_type="video/*",
    )

    # Prepare the prompt part
    prompt = _build_gemini_prompt(prompt_text)
    prompt_part = types.Part.from_text(text=prompt)

    contents = [video_part, prompt_part]

    config = types.GenerateContentConfig(
        temperature=settings.gemini_temperature,
        top_p=settings.gemini_top_p,
        max_output_tokens=settings.gemini_max_output_tokens,
        response_modalities=["TEXT"],
    )

    logger.info(
        "Calling Gemini API for summarization",
        model=settings.gemini_model,
        video_url=video_url,
        prompt_length=len(prompt),
    )

    try:
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=contents,
            config=config,
        )
    except Exception as exc:
        logger.error("Gemini API call failed", error=str(exc))
        raise GeminiSummarizationError(f"Gemini API call failed: {exc}") from exc

    if not response.text:
        logger.error("Gemini returned empty response")
        raise GeminiSummarizationError("Gemini returned an empty response.")

    logger.info(
        "Gemini response received",
        response_length=len(response.text),
        response_preview=response.text[:200],
    )

    # Parse the response through the same pipeline as Playwright
    parser = ResponseParser()
    try:
        parsed = parser.parse(response.text)
        return parsed
    except Exception as exc:
        logger.error("Failed to parse Gemini response as JSON", error=str(exc))
        raise GeminiSummarizationError(
            f"Gemini response could not be parsed as JSON: {exc}"
        ) from exc
