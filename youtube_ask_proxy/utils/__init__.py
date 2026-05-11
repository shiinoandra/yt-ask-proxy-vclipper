"""Utility helpers for retries, string processing, and DOM operations."""

import json
import random
import re
import time
from collections.abc import Callable
from typing import Any, TypeVar

from tenacity import (
    retry as tenacity_retry,
)
from tenacity import (
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from youtube_ask_proxy.config import settings
from youtube_ask_proxy.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


def with_retry(
    max_retries: int | None = None,
    base_delay: float | None = None,
    max_delay: float | None = None,
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator that adds exponential backoff retry logic.

    Args:
        max_retries: Maximum number of retry attempts. Defaults to settings.max_retries.
        base_delay: Initial delay between retries in seconds. Defaults to settings.retry_base_delay.
        max_delay: Maximum delay between retries in seconds. Defaults to settings.retry_max_delay.
        exceptions: Tuple of exception types to catch and retry on.

    Returns:
        A decorator function.
    """
    _max_retries = max_retries if max_retries is not None else settings.max_retries
    _base_delay = base_delay if base_delay is not None else settings.retry_base_delay
    _max_delay = max_delay if max_delay is not None else settings.retry_max_delay

    return tenacity_retry(
        stop=stop_after_attempt(_max_retries),
        wait=wait_exponential(multiplier=_base_delay, max=_max_delay),
        retry=retry_if_exception_type(exceptions),
        reraise=True,
        before_sleep=lambda retry_state: logger.warning(
            "Retrying after exception",
            attempt=retry_state.attempt_number,
            exception=str(retry_state.outcome.exception()) if retry_state.outcome else None,
        ),
    )


def humanized_delay(min_ms: int = 100, max_ms: int = 500) -> None:
    """Sleep for a randomized duration to mimic human behavior.

    Args:
        min_ms: Minimum delay in milliseconds.
        max_ms: Maximum delay in milliseconds.
    """
    delay = random.uniform(min_ms, max_ms) / 1000.0
    time.sleep(delay)


def strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences from a string.

    Args:
        text: Input text that may contain markdown fences.

    Returns:
        Cleaned text without markdown fences.
    """
    # Remove ```json ... ``` or ``` ... ``` blocks
    pattern = r"```(?:json)?\s*\n?(.*?)\n?```"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


def extract_json_objects(text: str) -> list[dict[str, Any]]:
    """Extract all valid JSON objects from a text string.

    Args:
        text: Input text that may contain JSON objects.

    Returns:
        List of parsed JSON objects.
    """
    objects: list[dict[str, Any]] = []
    # Try to find JSON objects using brace matching
    decoder = json.JSONDecoder()
    idx = 0
    text = text.strip()
    while idx < len(text):
        try:
            # Find next opening brace
            brace_idx = text.find("{", idx)
            if brace_idx == -1:
                break
            obj, end = decoder.raw_decode(text, brace_idx)
            if isinstance(obj, dict):
                objects.append(obj)
            idx = brace_idx + end
        except (json.JSONDecodeError, ValueError):
            idx += 1
    return objects


def repair_json(text: str) -> str:
    """Attempt to repair common JSON formatting issues.

    Args:
        text: Potentially malformed JSON string.

    Returns:
        Repaired JSON string.
    """
    repaired = text.strip()

    # Remove trailing commas before closing braces/brackets
    repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)

    # Add missing closing braces
    open_braces = repaired.count("{") - repaired.count("}")
    if open_braces > 0:
        repaired += "}" * open_braces

    # Add missing closing brackets
    open_brackets = repaired.count("[") - repaired.count("]")
    if open_brackets > 0:
        repaired += "]" * open_brackets

    # Fix single quotes to double quotes (basic)
    # This is a naive approach; only handles simple cases
    if "'" in repaired and '"' not in repaired:
        repaired = repaired.replace("'", '"')

    return repaired


def clean_extracted_text(text: str) -> str:
    """Clean raw text extracted from DOM.

    Args:
        text: Raw text from browser DOM.

    Returns:
        Cleaned text with normalized whitespace.
    """
    # Replace non-breaking spaces and other common HTML entities
    text = text.replace("\xa0", " ")
    text = text.replace("&nbsp;", " ")
    text = text.replace("&quot;", '"')
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")

    # Normalize whitespace
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def build_semantic_selector(*attributes: str, tag: str = "*") -> str:
    """Build a resilient CSS selector from semantic attributes.

    Args:
        attributes: Attribute names to search for (e.g., 'aria-label', 'data-testid').
        tag: HTML tag name.

    Returns:
        A CSS selector string.
    """
    parts = []
    for attr in attributes:
        parts.append(f"{tag}[{attr}]")
    return ", ".join(parts)


def truncate_string(text: str, max_length: int = 500) -> str:
    """Truncate a string with ellipsis.

    Args:
        text: Input string.
        max_length: Maximum length before truncation.

    Returns:
        Truncated string.
    """
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."
