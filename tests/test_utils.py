"""Tests for utility functions."""

from youtube_ask_proxy.utils import (
    clean_extracted_text,
    extract_json_objects,
    humanized_delay,
    repair_json,
    strip_markdown_fences,
    truncate_string,
)


class TestStripMarkdownFences:
    def test_json_fence(self) -> None:
        text = '```json\n{"key": "value"}\n```'
        assert strip_markdown_fences(text) == '{"key": "value"}'

    def test_plain_fence(self) -> None:
        text = "```\nhello world\n```"
        assert strip_markdown_fences(text) == "hello world"

    def test_no_fence(self) -> None:
        text = "just plain text"
        assert strip_markdown_fences(text) == "just plain text"


class TestExtractJsonObjects:
    def test_single_object(self) -> None:
        text = 'prefix {"a": 1} suffix'
        objs = extract_json_objects(text)
        assert len(objs) == 1
        assert objs[0] == {"a": 1}

    def test_multiple_objects(self) -> None:
        text = '{"a": 1} and {"b": 2}'
        objs = extract_json_objects(text)
        assert len(objs) == 2

    def test_nested_object(self) -> None:
        text = '{"outer": {"inner": true}}'
        objs = extract_json_objects(text)
        assert objs[0] == {"outer": {"inner": True}}

    def test_no_objects(self) -> None:
        text = "no json here"
        assert extract_json_objects(text) == []


class TestRepairJson:
    def test_trailing_comma(self) -> None:
        assert repair_json('{"a": 1,}') == '{"a": 1}'

    def test_missing_brace(self) -> None:
        assert repair_json('{"a": 1') == '{"a": 1}'

    def test_single_quotes(self) -> None:
        assert repair_json("{'a': 1}") == '{"a": 1}'


class TestCleanExtractedText:
    def test_nbsp(self) -> None:
        assert clean_extracted_text("hello\xa0world") == "hello world"

    def test_html_entities(self) -> None:
        assert clean_extracted_text("&amp; &lt;test&gt;") == "& <test>"

    def test_whitespace_normalization(self) -> None:
        assert clean_extracted_text("  multiple   spaces  ") == "multiple spaces"


class TestTruncateString:
    def test_short_string(self) -> None:
        assert truncate_string("hi", 10) == "hi"

    def test_long_string(self) -> None:
        text = "a" * 100
        result = truncate_string(text, 10)
        assert result == "aaaaaaa..."
        assert len(result) == 10


class TestHumanizedDelay:
    def test_runs_without_error(self) -> None:
        import time

        start = time.time()
        humanized_delay(50, 100)
        elapsed = time.time() - start
        assert 0.04 <= elapsed <= 0.15
