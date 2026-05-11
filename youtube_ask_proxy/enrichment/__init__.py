"""Enrichment utilities for merging auxiliary data into responses.

When the primary summarization engines (Playwright / Gemini video) succeed,
auxiliary data (captions, comments, live chat) can be used to enrich the
response with additional context, viewer reactions, and alternative moments.

When both primary engines fail, auxiliary data acts as a last-ditch fallback
to prevent completely empty responses.
"""

from __future__ import annotations

from typing import Any

from youtube_ask_proxy.logging import get_logger

logger = get_logger(__name__)


def merge_responses(
    base: dict[str, Any],
    auxiliary: dict[str, Any],
    enrichment_mode: bool = False,
) -> dict[str, Any]:
    """Merge an auxiliary response into a base response.

    In **enrichment mode** (``enrichment_mode=True``), the auxiliary moments
    are appended to the base moments list, and the auxiliary summary topics
    are merged into the base summary. This produces a richer, more
    comprehensive response.

    In **fallback mode** (``enrichment_mode=False``, default), the auxiliary
    response simply overwrites the base. This is used when the base is an
    empty or error placeholder.

    Args:
        base: Base response dict (from Playwright or Gemini video).
        auxiliary: Auxiliary response dict (from text LLM).
        enrichment_mode: Whether to merge (True) or replace (False).

    Returns:
        Merged response dict.
    """
    if not enrichment_mode:
        # Fallback mode: auxiliary replaces base entirely
        return auxiliary

    # Enrichment mode: merge auxiliary into base
    result: dict[str, Any] = {
        "_enriched": True,
    }

    # Merge summaries
    base_summary = base.get("summary", {})
    aux_summary = auxiliary.get("summary", {})

    merged_topics = list(dict.fromkeys(
        base_summary.get("main_topics", [])
        + aux_summary.get("main_topics", [])
    ))

    result["summary"] = {
        "main_topics": merged_topics,
        "overall_summary": (
            f"{base_summary.get('overall_summary', '')}\n\n"
            f"[Additional context from viewer data]:\n"
            f"{aux_summary.get('overall_summary', '')}"
        ).strip(),
    }

    # Merge moments: deduplicate by title
    base_moments = base.get("moments", [])
    aux_moments = auxiliary.get("moments", [])

    seen_titles: set[str] = set()
    merged_moments: list[dict[str, Any]] = []

    for moment in base_moments + aux_moments:
        title = moment.get("title", "")
        if title and title not in seen_titles:
            seen_titles.add(title)
            merged_moments.append(moment)

    result["moments"] = merged_moments

    logger.info(
        "Merged responses",
        base_moments=len(base_moments),
        aux_moments=len(aux_moments),
        merged_moments=len(merged_moments),
        merged_topics=len(merged_topics),
    )
    return result


def is_empty_or_error(response: dict[str, Any] | None) -> bool:
    """Check if a response is effectively empty or an error placeholder.

    Args:
        response: Response dict to check.

    Returns:
        True if the response is None, empty, or contains an error flag.
    """
    if response is None:
        return True
    if not response:
        return True
    if response.get("error") is True:
        return True
    if not response.get("moments") and not response.get("summary"):
        return True
    return False
