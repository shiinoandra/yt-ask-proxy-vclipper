"""Prompt construction and handling for YouTube Ask."""

from youtube_ask_proxy.logging import get_logger
from youtube_ask_proxy.models import ChatCompletionMessage

logger = get_logger(__name__)

# Default analysis template optimised for VTuber / livestream content.
# Loaded from the project's canonical prompt file so that it can be maintained
# outside of code if desired.
DEFAULT_ANALYSIS_TEMPLATE = """\
Role: Expert VTuber/Gaming Content Analyst.
Task: Analyze the provided video and output a structured JSON report for clip extraction and indexing.

Clip Criteria: Extract moments (max 5m) featuring funny reactions, panic/screams, fails, trolling, high chemistry, or chaotic interactions. Ignore filler.

JSON Schema:

{
  "summary": {
    "main_topics": ["topic1", "topic2"],
    "overall_summary": "Detailed summary of stream content, atmosphere, progression, and streamer energy."
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


class PromptBuilder:
    """Build and format prompts for the YouTube Ask feature."""

    def __init__(self) -> None:
        self._system_prompt: str | None = None
        self._user_messages: list[str] = []

    def from_chat_messages(self, messages: list[ChatCompletionMessage]) -> "PromptBuilder":
        """Build prompt from OpenAI-style chat messages.

        Args:
            messages: List of chat completion messages.

        Returns:
            Self for chaining.
        """
        self._system_prompt = None
        self._user_messages = []

        for msg in messages:
            if msg.role == "system":
                self._system_prompt = msg.content or ""
            elif msg.role == "user":
                self._user_messages.append(msg.content or "")
            elif msg.role == "assistant":
                # Include assistant messages as context
                self._user_messages.append(f"Assistant: {msg.content or ''}")

        return self

    def build(self) -> str:
        """Build the final prompt string.

        When using the default VTuber analysis template (or any custom
        ``PROMPT_TEMPLATE``), the template itself is the complete prompt.
        ``{system}`` and ``{user}`` placeholders are replaced with any
        extra instructions or the user's specific query, but the bulk of
        the prompt is already inside the template.

        Returns:
            Formatted prompt string.
        """
        user_text = "\n\n".join(self._user_messages) if self._user_messages else ""
        system_text = self._system_prompt or ""

        from youtube_ask_proxy.config import settings

        template = settings.prompt_template or DEFAULT_ANALYSIS_TEMPLATE
        # Use .replace() rather than .format() so that literal braces in the
        # JSON schema example (and any other text) are not misinterpreted as
        # format placeholders.
        prompt = template.replace("{system}", system_text).replace("{user}", user_text)
        logger.debug(
            "Built prompt",
            prompt_length=len(prompt),
            user_messages=len(self._user_messages),
            template_source="custom" if settings.prompt_template else "default",
        )
        return prompt

        # Default minimal concatenation
        parts: list[str] = []
        if system_text:
            parts.append(f"Instructions: {system_text}")
        if user_text:
            parts.append(user_text)
        else:
            parts.append("Please provide a summary or answer based on the video content.")

        prompt = "\n\n".join(parts)
        logger.debug(
            "Built prompt", prompt_length=len(prompt), user_messages=len(self._user_messages)
        )
        return prompt

    def extract_video_url(self) -> str | None:
        """Attempt to extract a YouTube video URL from the user messages.

        Returns:
            YouTube video URL if found, None otherwise.
        """
        import re

        youtube_patterns = [
            r"https?://(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]+)",
            r"https?://(?:www\.)?youtu\.be/([a-zA-Z0-9_-]+)",
            r"https?://(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]+)",
        ]

        for message in self._user_messages:
            for pattern in youtube_patterns:
                match = re.search(pattern, message or "")
                if match:
                    video_id = match.group(1)
                    url = f"https://www.youtube.com/watch?v={video_id}"
                    logger.debug("Extracted video URL from message", video_id=video_id)
                    return url

        return None


def build_ask_prompt(
    messages: list[ChatCompletionMessage],
    video_url: str | None = None,
) -> tuple[str, str | None]:
    """Convenience function to build prompt and extract video URL.

    Args:
        messages: OpenAI-style chat messages.
        video_url: Optional explicit video URL.

    Returns:
        Tuple of (prompt_text, resolved_video_url).
    """
    builder = PromptBuilder().from_chat_messages(messages)
    prompt = builder.build()
    resolved_url = video_url or builder.extract_video_url()
    return prompt, resolved_url
