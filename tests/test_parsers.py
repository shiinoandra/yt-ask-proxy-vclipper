"""Tests for response parser."""

import pytest

from youtube_ask_proxy.parsers import ParseError, ResponseParser


class TestResponseParser:
    def test_parse_valid_json(self) -> None:
        parser = ResponseParser()
        raw = '{"response": "This is a test", "confidence": 0.95}'
        result = parser.parse(raw)
        assert result["response"] == "This is a test"
        assert result["confidence"] == 0.95
        assert result["_parsed"] is True

    def test_parse_markdown_fences(self) -> None:
        parser = ResponseParser()
        raw = '```json\n{"response": "hello"}\n```'
        result = parser.parse(raw)
        assert result["response"] == "hello"
        assert result["_parsed"] is True

    def test_parse_with_extra_text(self) -> None:
        parser = ResponseParser()
        raw = 'Here is the result:\n\n{"response": "found it"}\n\nHope that helps!'
        result = parser.parse(raw)
        assert result["response"] == "found it"
        assert result["_parsed"] is True

    def test_parse_repair_json(self) -> None:
        parser = ResponseParser()
        raw = '{"response": "test",}'  # trailing comma
        result = parser.parse(raw)
        assert result["response"] == "test"

    def test_parse_fallback_on_unparseable(self) -> None:
        parser = ResponseParser()
        raw = "This is just plain text with no JSON at all"
        result = parser.parse(raw)
        assert result["response"] == raw
        assert result["_parsed"] is False

    def test_parse_empty_raises(self) -> None:
        parser = ResponseParser()
        with pytest.raises(ParseError):
            parser.parse("")

    def test_validate_schema(self) -> None:
        parser = ResponseParser()
        assert parser.validate_schema({"response": "ok"}) is True
        assert parser.validate_schema({"other": "ok"}) is False
        assert parser.validate_schema({"response": "ok"}, ["response", "extra"]) is False
