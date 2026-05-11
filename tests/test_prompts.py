"""Tests for prompt builder."""

from typing import Any

from youtube_ask_proxy.models import ChatCompletionMessage
from youtube_ask_proxy.prompts import PromptBuilder, build_ask_prompt


class TestPromptBuilder:
    def test_build_simple_prompt(self) -> None:
        builder = PromptBuilder()
        messages = [
            ChatCompletionMessage(role="user", content="Summarize this video"),
        ]
        builder.from_chat_messages(messages)
        prompt = builder.build()
        assert "Summarize this video" in prompt

    def test_build_with_system_message(self) -> None:
        builder = PromptBuilder()
        messages = [
            ChatCompletionMessage(role="system", content="Be concise"),
            ChatCompletionMessage(role="user", content="What is this about?"),
        ]
        builder.from_chat_messages(messages)
        prompt = builder.build()
        # The default VTuber analysis template embeds {system} near the end
        assert "Be concise" in prompt
        assert "What is this about?" in prompt
        assert "VTuber" in prompt  # Confirm the default template is being used

    def test_extract_video_url_standard(self) -> None:
        builder = PromptBuilder()
        messages = [
            ChatCompletionMessage(
                role="user",
                content="Check out https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            ),
        ]
        builder.from_chat_messages(messages)
        url = builder.extract_video_url()
        assert url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_extract_video_url_short(self) -> None:
        builder = PromptBuilder()
        messages = [
            ChatCompletionMessage(
                role="user",
                content="https://youtu.be/dQw4w9WgXcQ is great",
            ),
        ]
        builder.from_chat_messages(messages)
        url = builder.extract_video_url()
        assert url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_extract_video_url_none(self) -> None:
        builder = PromptBuilder()
        messages = [
            ChatCompletionMessage(role="user", content="Hello there"),
        ]
        builder.from_chat_messages(messages)
        url = builder.extract_video_url()
        assert url is None


class TestPromptTemplate:
    def test_custom_template(self, monkeypatch: Any) -> None:
        from youtube_ask_proxy.config import settings as global_settings

        custom_template = "SYSTEM: {system}\n\nUSER: {user}\n\nAnswer in JSON."
        original_template = global_settings.prompt_template
        monkeypatch.setattr(global_settings, "prompt_template", custom_template)
        try:
            builder = PromptBuilder()
            messages = [
                ChatCompletionMessage(role="system", content="Be concise"),
                ChatCompletionMessage(role="user", content="Summarize"),
            ]
            builder.from_chat_messages(messages)
            prompt = builder.build()
            assert "SYSTEM: Be concise" in prompt
            assert "USER: Summarize" in prompt
            assert "Answer in JSON." in prompt
        finally:
            monkeypatch.setattr(global_settings, "prompt_template", original_template)


class TestBuildAskPrompt:
    def test_build_with_explicit_url(self) -> None:
        messages = [ChatCompletionMessage(role="user", content="Summarize")]
        prompt, url = build_ask_prompt(messages, video_url="https://youtube.com/watch?v=abc123")
        assert url == "https://youtube.com/watch?v=abc123"
        assert "Summarize" in prompt

    def test_build_with_inferred_url(self) -> None:
        messages = [
            ChatCompletionMessage(role="user", content="https://youtu.be/abc123 what is this?"),
        ]
        prompt, url = build_ask_prompt(messages)
        assert url == "https://www.youtube.com/watch?v=abc123"

    def test_build_no_url(self) -> None:
        messages = [ChatCompletionMessage(role="user", content="Hello")]
        prompt, url = build_ask_prompt(messages)
        assert url is None
        assert "Hello" in prompt
