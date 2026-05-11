"""conftest.py - Shared test fixtures."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(scope="session", autouse=True)
def patch_browser_for_tests():
    """Patch browser controller to prevent real browser startup during tests."""
    mock_controller = MagicMock()
    mock_controller._closed = False
    mock_controller.ask = AsyncMock(return_value={"response": "mock summary"})

    # Patch the lifespan to skip browser pre-launch
    async def mock_lifespan(app):
        from youtube_ask_proxy.logging import configure_logging
        configure_logging()
        yield

    with patch("youtube_ask_proxy.api.lifespan", new=mock_lifespan):
        yield
