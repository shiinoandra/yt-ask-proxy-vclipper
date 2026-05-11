"""Tests for FastAPI endpoints."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from youtube_ask_proxy.api import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestListModels:
    def test_list_models(self, client: TestClient) -> None:
        response = client.get("/v1/models")
        assert response.status_code == 200
        data = response.json()
        assert data["object"] == "list"
        assert len(data["data"]) >= 1
        assert data["data"][0]["id"] == "youtube-ask-proxy"


class TestChatCompletions:
    def test_missing_video_url(self, client: TestClient) -> None:
        payload = {
            "model": "youtube-ask-proxy",
            "messages": [{"role": "user", "content": "Hello"}],
        }
        response = client.post("/v1/chat/completions", json=payload)
        assert response.status_code == 400
        error = response.json()["error"]
        assert "video url" in error["message"].lower()

    @patch("youtube_ask_proxy.api._summarize_with_gemini")
    def test_with_video_url_in_message(
        self, mock_gemini: AsyncMock, client: TestClient
    ) -> None:
        mock_gemini.return_value = {"response": "This is a summary."}

        payload = {
            "model": "youtube-ask-proxy",
            "messages": [
                {
                    "role": "user",
                    "content": "https://www.youtube.com/watch?v=dQw4w9WgXcQ summarize",
                }
            ],
        }
        response = client.post("/v1/chat/completions", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["object"] == "chat.completion"
        assert len(data["choices"]) == 1
        assert "This is a summary." in data["choices"][0]["message"]["content"]
        mock_gemini.assert_called_once()

    @patch("youtube_ask_proxy.api._summarize_with_gemini")
    def test_stream_response(self, mock_gemini: AsyncMock, client: TestClient) -> None:
        mock_gemini.return_value = {"response": "Streamed result."}

        payload = {
            "model": "youtube-ask-proxy",
            "messages": [
                {
                    "role": "user",
                    "content": "https://youtu.be/dQw4w9WgXcQ test",
                }
            ],
            "stream": True,
        }
        response = client.post("/v1/chat/completions", json=payload)
        assert response.status_code == 200
        # FastAPI TestClient handles StreamingResponse by reading it fully
        text = response.text
        assert "Streamed result." in text
        assert "[DONE]" in text
        mock_gemini.assert_called_once()

    @patch("youtube_ask_proxy.api._summarize_with_gemini")
    @patch("youtube_ask_proxy.api._summarize_with_playwright")
    def test_fallback_to_playwright(
        self, mock_playwright: AsyncMock, mock_gemini: AsyncMock, client: TestClient
    ) -> None:
        """Test that Playwright is used when Gemini fails."""
        mock_gemini.return_value = None  # Gemini fails
        mock_playwright.return_value = {"response": "Playwright fallback result."}

        payload = {
            "model": "youtube-ask-proxy",
            "messages": [
                {
                    "role": "user",
                    "content": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                }
            ],
        }
        response = client.post("/v1/chat/completions", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert "Playwright fallback result." in data["choices"][0]["message"]["content"]
        mock_gemini.assert_called_once()
        mock_playwright.assert_called_once()

    @patch("youtube_ask_proxy.api._summarize_with_gemini")
    @patch("youtube_ask_proxy.api._summarize_with_playwright")
    def test_both_methods_fail_gracefully(
        self, mock_playwright: AsyncMock, mock_gemini: AsyncMock, client: TestClient
    ) -> None:
        """Test graceful degradation when both methods fail."""
        mock_gemini.return_value = None
        mock_playwright.return_value = None

        payload = {
            "model": "youtube-ask-proxy",
            "messages": [
                {
                    "role": "user",
                    "content": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                }
            ],
        }
        response = client.post("/v1/chat/completions", json=payload)
        assert response.status_code == 200
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        parsed = __import__("json").loads(content)
        assert parsed["error"] is True
        assert "not available" in parsed["message"].lower()
