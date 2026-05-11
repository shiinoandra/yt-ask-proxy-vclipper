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

    @patch("youtube_ask_proxy.api._get_browser_controller")
    def test_with_video_url_in_message(
        self, mock_get_controller: AsyncMock, client: TestClient
    ) -> None:
        mock_controller = AsyncMock()
        mock_controller.ask = AsyncMock(return_value={"response": "This is a summary."})
        mock_get_controller.return_value = mock_controller

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
        mock_controller.ask.assert_called_once()

    @patch("youtube_ask_proxy.api._get_browser_controller")
    def test_stream_response(self, mock_get_controller: AsyncMock, client: TestClient) -> None:
        mock_controller = AsyncMock()
        mock_controller.ask = AsyncMock(return_value={"response": "Streamed result."})
        mock_get_controller.return_value = mock_controller

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
        mock_controller.ask.assert_called_once()
