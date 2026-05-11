"""Configuration management using Pydantic Settings."""

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # API Server
    api_host: str = Field(default="0.0.0.0", description="Host to bind the API server")
    api_port: int = Field(default=8000, ge=1, le=65535, description="Port to bind the API server")
    api_workers: int = Field(default=1, ge=1, description="Number of API worker processes")
    api_key: str | None = Field(default=None, description="Optional API key for authentication")

    # Browser
    browser_type: Literal["chromium", "firefox", "webkit"] = Field(
        default="chromium", description="Browser type for Playwright"
    )
    headless: bool = Field(default=True, description="Run browser in headless mode")
    slow_mo: int = Field(default=50, ge=0, description="Slow down Playwright operations by ms")
    browser_timeout: int = Field(
        default=30000, ge=1000, description="Default timeout for browser operations in ms"
    )
    navigation_timeout: int = Field(
        default=60000, ge=1000, description="Page navigation timeout in ms"
    )
    response_timeout: int = Field(
        default=120000, ge=5000, description="Timeout waiting for AI response in ms"
    )

    # Dynamic rendering waits (critical for server-side injected Ask UI)
    page_settle_timeout: int = Field(
        default=10000, ge=1000, description="Wait for page JS to settle after domcontentloaded (ms)"
    )
    ask_feature_detection_timeout: int = Field(
        default=20000, ge=1000, description="Max time to poll for Ask button appearance (ms)"
    )
    ask_panel_open_timeout: int = Field(
        default=15000,
        ge=1000,
        description="Max time to wait for Ask panel to open after click (ms)",
    )
    ask_poll_interval: float = Field(
        default=0.5, ge=0.1, description="Interval between Ask UI polling attempts (seconds)"
    )

    # Browser Profile / Auth
    user_data_dir: Path | None = Field(
        default=Path("./browser_data"),
        description="Path to persistent browser user data directory",
    )
    cookies_file: Path | None = Field(
        default=None, description="Path to saved cookies file for session restoration"
    )
    auth_required: bool = Field(
        default=True, description="Whether authentication is required for YouTube Ask"
    )

    # Stealth / Anti-bot
    stealth_enabled: bool = Field(
        default=True, description="Enable anti-detection stealth patches (recommended for Google auth)"
    )
    user_agent: str | None = Field(
        default=None,
        description="Custom User-Agent string (None = default browser)",
    )
    viewport_width: int = Field(default=1920, description="Browser viewport width")
    viewport_height: int = Field(default=1080, description="Browser viewport height")
    locale: str = Field(default="en-US", description="Browser locale")
    timezone: str = Field(default="America/New_York", description="Browser timezone")

    # Reliability
    max_retries: int = Field(default=3, ge=0, description="Max retries for failed operations")
    retry_base_delay: float = Field(
        default=1.0, ge=0.1, description="Base delay for exponential backoff in seconds"
    )
    retry_max_delay: float = Field(
        default=30.0, ge=1.0, description="Max delay for exponential backoff in seconds"
    )

    # Observability
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", description="Logging level"
    )
    log_format: Literal["json", "console"] = Field(
        default="console", description="Log output format"
    )
    capture_screenshots: bool = Field(
        default=False, description="Capture screenshots on browser failures"
    )
    screenshot_dir: Path = Field(
        default=Path("screenshots"), description="Directory for failure screenshots"
    )
    enable_tracing: bool = Field(default=False, description="Enable Playwright tracing")

    # YouTube
    youtube_base_url: str = Field(default="https://www.youtube.com", description="YouTube base URL")
    default_model: str = Field(
        default="youtube-ask-proxy", description="Default model name for API"
    )

    # Gemini API
    gemini_api_key: str | None = Field(
        default=None, description="Google Gemini API key for video summarization"
    )
    gemini_model: str = Field(
        default="gemini-3-flash-preview",
        description="Gemini model name (e.g. gemini-3-flash-preview, gemini-2.5-flash)",
    )
    gemini_temperature: float = Field(
        default=1.0, ge=0.0, le=2.0, description="Gemini generation temperature"
    )
    gemini_top_p: float = Field(
        default=0.95, ge=0.0, le=1.0, description="Gemini top-p sampling"
    )
    gemini_max_output_tokens: int = Field(
        default=8192, ge=1, le=8192, description="Gemini max output tokens"
    )
    gemini_timeout: int = Field(
        default=120000, ge=5000, description="Gemini API timeout in ms"
    )
    gemini_enabled: bool = Field(
        default=True, description="Enable Gemini API summarization (fallback method)"
    )

    # OpenAI-compatible API (optional, for text-based auxiliary summarization)
    openai_base_url: str | None = Field(
        default=None, description="Base URL for OpenAI-compatible API (e.g. https://api.openai.com/v1)"
    )
    openai_api_key: str | None = Field(
        default=None, description="API key for OpenAI-compatible API"
    )
    openai_model: str | None = Field(
        default=None, description="Model name for OpenAI-compatible API (e.g. gpt-4o-mini)"
    )
    openai_temperature: float = Field(
        default=1.0, ge=0.0, le=2.0, description="Temperature for OpenAI-compatible API"
    )
    openai_max_tokens: int = Field(
        default=8192, ge=1, description="Max output tokens for OpenAI-compatible API"
    )
    openai_timeout: int = Field(
        default=120000, ge=5000, description="Timeout for OpenAI-compatible API in ms"
    )

    # Prompting
    prompt_template: str | None = Field(
        default=None,
        description=(
            "Optional Jinja2-style template for wrapping user prompts. "
            "Use {system} for system instructions and {user} for the user message. "
            "If unset, a minimal concatenation is used."
        ),
    )

    @field_validator("user_data_dir", "cookies_file", "screenshot_dir", mode="before")
    @classmethod
    def resolve_path(cls, v: str | Path | None) -> Path | None:
        """Resolve string paths to Path objects."""
        if v is None:
            return None
        if isinstance(v, str):
            return Path(v).expanduser().resolve()
        return v.expanduser().resolve()


# Global settings instance
settings = Settings()
