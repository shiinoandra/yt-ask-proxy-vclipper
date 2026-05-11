"""Download auxiliary YouTube data (captions, comments, live chat).

Used as a last-resort fallback when both Playwright and Gemini API fail.
The extracted text data is sent to an LLM for summarization and moment
extraction, then merged into the final response.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from youtube_ask_proxy.logging import get_logger

logger = get_logger(__name__)


def _extract_video_id(video_url: str) -> str:
    """Extract YouTube video ID from a URL."""
    patterns = [
        r"youtube\.com/watch\?v=([a-zA-Z0-9_-]+)",
        r"youtu\.be/([a-zA-Z0-9_-]+)",
        r"youtube\.com/shorts/([a-zA-Z0-9_-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, video_url)
        if match:
            return match.group(1)
    raise ValueError(f"Could not extract video ID from URL: {video_url}")


def fetch_captions(video_url: str) -> str | None:
    """Fetch caption/transcript text for a YouTube video.

    Compatible with ``youtube-transcript-api`` v1.x where ``fetch()`` and
    ``list()`` are instance methods that take ``video_id`` as an argument.

    Args:
        video_url: Full YouTube video URL.

    Returns:
        Transcript text or None if unavailable.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        video_id = _extract_video_id(video_url)
        logger.info("Fetching captions", video_id=video_id)

        # v1.x API: create instance (no args), then pass video_id to methods
        ytta = YouTubeTranscriptApi()

        # Try to list available transcripts and pick the best one
        entries: list[dict[str, Any]] = []
        try:
            transcript_list = ytta.list(video_id)
            # Priority: English first, then any other language
            selected = None
            for t in transcript_list:
                if getattr(t, "language_code", None) == "en":
                    selected = t
                    break
            if selected is None and transcript_list:
                selected = transcript_list[0]

            if selected is not None:
                fetched = selected.fetch()
                # FetchedTranscript is iterable of FetchedTranscriptSnippet
                entries = list(fetched)
        except Exception:
            # Fallback: try direct fetch (grabs best available)
            try:
                fetched = ytta.fetch(video_id)
                entries = list(fetched)
            except Exception:
                pass

        if not entries:
            logger.warning("No captions available", video_id=video_id)
            return None

        lines: list[str] = []
        for entry in entries:
            # FetchedTranscriptSnippet has a .text attribute
            text = getattr(entry, "text", "") or ""
            text = text.strip()
            if text:
                lines.append(text)

        full_text = "\n".join(lines)
        logger.info(
            "Captions fetched",
            video_id=video_id,
            lines=len(lines),
            chars=len(full_text),
        )
        return full_text

    except Exception as exc:
        logger.warning("Failed to fetch captions", error=str(exc))
        return None


def fetch_live_chat(video_url: str, max_messages: int = 500) -> str | None:
    """Fetch live chat replay for a YouTube video (if it was a live stream).

    Args:
        video_url: Full YouTube video URL.
        max_messages: Maximum chat messages to extract.

    Returns:
        Chat text or None if unavailable / not a live stream.
    """
    try:
        import yt_dlp

        video_id = _extract_video_id(video_url)
        logger.info("Fetching live chat", video_id=video_id, max_messages=max_messages)

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "writeinfojson": False,
            "extract_flat": False,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)

        if not info:
            return None

        # Check if live chat is available
        subtitles = info.get("subtitles") or {}
        live_chat = subtitles.get("live_chat")

        if not live_chat:
            logger.info("No live chat replay available", video_id=video_id)
            return None

        # Download live chat
        ydl_opts_chat = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "writesubtitles": True,
            "subtitleslangs": ["live_chat"],
            "subtitlesformat": "json3",
            "outtmpl": "%(id)s.%(ext)s",
        }

        chat_texts: list[str] = []
        with yt_dlp.YoutubeDL(ydl_opts_chat) as ydl:
            # Extract and get the live chat subtitle URL
            info2 = ydl.extract_info(video_url, download=False)
            subs = info2.get("subtitles") or {}
            live_chat_entries = subs.get("live_chat", [])

            if not live_chat_entries:
                return None

            # Download the live chat subtitle file
            import tempfile
            import json

            with tempfile.TemporaryDirectory() as tmpdir:
                ydl_opts_dl = {
                    "quiet": True,
                    "no_warnings": True,
                    "skip_download": True,
                    "writesubtitles": True,
                    "subtitleslangs": ["live_chat"],
                    "subtitlesformat": "json3",
                    "outtmpl": f"{tmpdir}/%(id)s.%(ext)s",
                }
                with yt_dlp.YoutubeDL(ydl_opts_dl) as ydl2:
                    ydl2.download([video_url])

                # Find and parse the downloaded JSON3 file
                import glob
                import os

                json3_files = glob.glob(f"{tmpdir}/*.live_chat.json3")
                if not json3_files:
                    return None

                with open(json3_files[0], "r", encoding="utf-8") as f:
                    chat_data = json.load(f)

                events = chat_data.get("events", [])
                for event in events[:max_messages]:
                    if "segs" in event:
                        text = "".join(seg.get("utf8", "") for seg in event["segs"])
                        text = text.strip()
                        if text and not text.startswith("\ufeff"):
                            chat_texts.append(text)

        if not chat_texts:
            return None

        full_text = "\n".join(chat_texts)
        logger.info(
            "Live chat fetched",
            video_id=video_id,
            messages=len(chat_texts),
            chars=len(full_text),
        )
        return full_text

    except Exception as exc:
        logger.warning("Failed to fetch live chat", error=str(exc))
        return None


def fetch_top_comments(video_url: str, max_comments: int = 30) -> str | None:
    """Fetch top-level comments for a YouTube video.

    Args:
        video_url: Full YouTube video URL.
        max_comments: Maximum top-level comments to extract.

    Returns:
        Comment text or None if unavailable.
    """
    try:
        import yt_dlp

        video_id = _extract_video_id(video_url)
        logger.info("Fetching comments", video_id=video_id, max_comments=max_comments)

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,
            "getcomments": True,
            "comment_count": max_comments,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)

        if not info:
            return None

        comments = info.get("comments", [])
        if not comments:
            logger.info("No comments available", video_id=video_id)
            return None

        texts: list[str] = []
        for comment in comments[:max_comments]:
            text = comment.get("text", "").strip()
            if text:
                texts.append(text)

        if not texts:
            return None

        full_text = "\n".join(texts)
        logger.info(
            "Comments fetched",
            video_id=video_id,
            comments=len(texts),
            chars=len(full_text),
        )
        return full_text

    except Exception as exc:
        logger.warning("Failed to fetch comments", error=str(exc))
        return None


def build_auxiliary_context(
    video_url: str,
    captions: str | None = None,
    comments: str | None = None,
    live_chat: str | None = None,
) -> dict[str, Any]:
    """Build an auxiliary context dict from available text sources.

    Args:
        video_url: YouTube video URL.
        captions: Caption text (optional).
        comments: Comment text (optional).
        live_chat: Live chat text (optional).

    Returns:
        Dictionary with ``available_sources``, ``total_chars``, and ``text``.
    """
    parts: list[str] = []
    sources: list[str] = []

    if captions:
        parts.append(f"--- VIDEO CAPTIONS ---\n{captions}")
        sources.append("captions")

    if live_chat:
        parts.append(f"--- LIVE CHAT ---\n{live_chat}")
        sources.append("live_chat")

    if comments:
        parts.append(f"--- TOP COMMENTS ---\n{comments}")
        sources.append("comments")

    full_text = "\n\n".join(parts)
    return {
        "available_sources": sources,
        "total_chars": len(full_text),
        "text": full_text,
    }


async def fetch_all_auxiliary_data(
    video_url: str,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Fetch all available auxiliary data for a video.

    This is a convenience wrapper that attempts to download captions,
    live chat, and comments, then builds a unified context object.

    All blocking I/O (yt_dlp) is offloaded to threads so the asyncio
    event loop is not blocked.

    Args:
        video_url: YouTube video URL.
        timeout_seconds: Max time to wait for all auxiliary downloads.

    Returns:
        Context dict from :func:`build_auxiliary_context`.
    """
    logger.info(
        "Fetching auxiliary data",
        video_url=video_url,
        timeout_seconds=timeout_seconds,
    )

    # Run blocking functions in threads so the event loop stays free
    loop = asyncio.get_event_loop()
    captions_task = loop.run_in_executor(None, fetch_captions, video_url)
    live_chat_task = loop.run_in_executor(None, fetch_live_chat, video_url)
    comments_task = loop.run_in_executor(None, fetch_top_comments, video_url)

    try:
        captions, live_chat, comments = await asyncio.wait_for(
            asyncio.gather(captions_task, live_chat_task, comments_task),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        logger.warning("Auxiliary data fetching timed out")
        # Cancel any remaining tasks
        for task in (captions_task, live_chat_task, comments_task):
            if not task.done():
                task.cancel()
        # Use whatever finished before timeout
        captions = captions_task.result() if captions_task.done() else None
        live_chat = live_chat_task.result() if live_chat_task.done() else None
        comments = comments_task.result() if comments_task.done() else None

    context = build_auxiliary_context(video_url, captions, comments, live_chat)
    logger.info(
        "Auxiliary data ready",
        sources=context["available_sources"],
        total_chars=context["total_chars"],
    )
    return context
