"""Response parsing and validation logic."""

import json
from typing import Any

from youtube_ask_proxy.logging import get_logger
from youtube_ask_proxy.utils import (
    clean_extracted_text,
    extract_json_objects,
    repair_json,
    strip_markdown_fences,
    truncate_string,
)

logger = get_logger(__name__)


class ParseError(Exception):
    """Raised when response parsing fails."""

    pass


class ResponseParser:
    """Parse and normalize responses from YouTube Ask feature."""

    def __init__(self) -> None:
        self._fallback_schema: dict[str, Any] | None = None

    def parse(self, raw_text: str) -> dict[str, Any]:
        """Parse raw text into a structured JSON object.

        The parser attempts multiple strategies in order:
        1. Strip markdown fences and parse as JSON
        2. Extract JSON objects from surrounding text
        3. Repair malformed JSON and parse
        4. Return a wrapped object with the raw text

        Args:
            raw_text: Raw text extracted from the YouTube Ask UI.

        Returns:
            A dictionary representing the parsed response.

        Raises:
            ParseError: If all parsing strategies fail and fallback is disabled.
        """
        if not raw_text or not raw_text.strip():
            logger.error("Empty response text received")
            raise ParseError("Empty response text")

        cleaned = clean_extracted_text(raw_text)
        logger.debug("Parsing response", raw_length=len(raw_text), cleaned_length=len(cleaned))

        # Strategy 1: Direct JSON parse after stripping markdown
        text_no_fences = strip_markdown_fences(cleaned)
        try:
            parsed = json.loads(text_no_fences)
            if isinstance(parsed, dict):
                logger.debug("Parsed response via markdown fence stripping")
                return self._normalize(parsed)
        except json.JSONDecodeError:
            pass

        # Strategy 2: Extract JSON objects from text
        objects = extract_json_objects(cleaned)
        if objects:
            logger.debug("Extracted JSON objects from response", count=len(objects))
            return self._normalize(objects[0])

        # Strategy 3: Try to repair and parse
        repaired = repair_json(text_no_fences)
        try:
            parsed = json.loads(repaired)
            if isinstance(parsed, dict):
                logger.debug("Parsed response via JSON repair")
                return self._normalize(parsed)
        except json.JSONDecodeError:
            pass

        # Strategy 4: Wrap raw text as fallback
        logger.warning(
            "Failed to parse structured JSON, returning fallback wrapper",
            text_preview=truncate_string(cleaned, 200),
        )
        return {
            "response": cleaned,
            "_parsed": False,
            "_warning": "Response could not be parsed as structured JSON",
        }

    def _normalize(self, data: dict[str, Any]) -> dict[str, Any]:
        """Normalize a parsed dictionary.

        Args:
            data: Parsed dictionary.

        Returns:
            Normalized dictionary.
        """
        # Ensure basic fields exist
        if "response" not in data and "content" not in data:
            # If there's no standard field, wrap the whole object
            data = {"response": data}

        data["_parsed"] = True
        return data

    def validate_schema(
        self,
        data: dict[str, Any],
        required_keys: list[str] | None = None,
    ) -> bool:
        """Validate that parsed data contains required keys.

        Args:
            data: Parsed dictionary.
            required_keys: List of keys that must be present.

        Returns:
            True if valid, False otherwise.
        """
        if required_keys is None:
            required_keys = ["response"]

        missing = [k for k in required_keys if k not in data]
        if missing:
            logger.warning("Schema validation failed", missing_keys=missing)
            return False
        return True
