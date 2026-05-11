"""Authentication and session persistence for YouTube/Google."""

import json
from pathlib import Path
from typing import Any

from youtube_ask_proxy.config import settings
from youtube_ask_proxy.logging import get_logger

logger = get_logger(__name__)


class AuthManager:
    """Manage Google/YouTube authentication state and session persistence."""

    def __init__(self) -> None:
        self._cookies_file = settings.cookies_file
        self._user_data_dir = settings.user_data_dir

    @property
    def user_data_dir(self) -> Path | None:
        """Path to persistent browser user data directory."""
        return self._user_data_dir

    @property
    def has_persistent_profile(self) -> bool:
        """Check if a persistent browser profile is configured."""
        return self._user_data_dir is not None and self._user_data_dir.exists()

    def load_cookies(self) -> list[dict[str, Any]]:
        """Load cookies from the configured cookies file.

        Returns:
            List of cookie dictionaries.
        """
        if not self._cookies_file or not self._cookies_file.exists():
            logger.debug("No cookies file found", path=str(self._cookies_file))
            return []

        try:
            with open(self._cookies_file, encoding="utf-8") as f:
                cookies: list[dict[str, Any]] = json.load(f)
                logger.info("Loaded cookies from file", count=len(cookies))
                return cookies
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load cookies", error=str(e))
            return []

    def save_cookies(self, cookies: list[dict[str, Any]]) -> None:
        """Save cookies to the configured cookies file.

        Args:
            cookies: List of cookie dictionaries.
        """
        if not self._cookies_file:
            logger.debug("No cookies file configured, skipping save")
            return

        try:
            self._cookies_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._cookies_file, "w", encoding="utf-8") as f:
                json.dump(cookies, f, indent=2)
            logger.info("Saved cookies to file", count=len(cookies), path=str(self._cookies_file))
        except OSError as e:
            logger.warning("Failed to save cookies", error=str(e))

    async def apply_cookies_to_context(self, context: Any) -> None:
        """Apply saved cookies to a Playwright browser context.

        Args:
            context: Playwright BrowserContext instance.
        """
        cookies = self.load_cookies()
        if cookies:
            try:
                await context.add_cookies(cookies)
                logger.debug("Applied cookies to browser context", count=len(cookies))
            except Exception as e:
                logger.warning("Failed to apply cookies", error=str(e))

    async def extract_cookies_from_context(self, context: Any) -> None:
        """Extract and save cookies from a Playwright browser context.

        Args:
            context: Playwright BrowserContext instance.
        """
        try:
            cookies = await context.cookies()
            if cookies:
                self.save_cookies(cookies)
        except Exception as e:
            logger.warning("Failed to extract cookies", error=str(e))

    def ensure_user_data_dir(self) -> Path | None:
        """Ensure the user data directory exists.

        Returns:
            Path to the user data directory, or None if not configured.
        """
        if self._user_data_dir is None:
            return None
        self._user_data_dir.mkdir(parents=True, exist_ok=True)
        return self._user_data_dir
