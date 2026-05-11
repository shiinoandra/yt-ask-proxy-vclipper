"""Main application entry point."""

import argparse
import asyncio
import sys
from pathlib import Path

from youtube_ask_proxy.config import settings
from youtube_ask_proxy.logging import configure_logging, get_logger

logger = get_logger(__name__)


def _bootstrap_auth() -> None:
    """Interactive script to authenticate with YouTube and save session."""

    from playwright.async_api import async_playwright

    from youtube_ask_proxy.auth import AuthManager
    from youtube_ask_proxy.stealth import apply_stealth_to_page, get_stealth_args

    async def _run() -> None:
        configure_logging()
        logger.info("Starting authentication bootstrap")
        print("=" * 60)
        print("YouTube Ask Proxy - Authentication Bootstrap")
        print("=" * 60)
        print()
        print("A browser window will open. Please sign in to your Google account.")
        print("After signing in, close the browser to save the session.")
        print()

        auth_manager = AuthManager()
        user_data_dir = auth_manager.ensure_user_data_dir()
        if user_data_dir is None:
            print("WARNING: No USER_DATA_DIR configured. Session will not persist!")
            user_data_dir = Path("./browser_data")

        launch_args = get_stealth_args()

        logger.info("Launching browser for auth", profile_dir=str(user_data_dir))

        async with async_playwright() as p:
            browser = await p.chromium.launch_persistent_context(
                user_data_dir=str(user_data_dir),
                headless=False,
                slow_mo=100,
                viewport={"width": 1920, "height": 1080},
                args=launch_args,
            )
            page = await browser.new_page()
            await apply_stealth_to_page(page)
            await page.goto("https://www.youtube.com")
            print("Browser opened. Please sign in to YouTube.")
            print("Press Ctrl+C or close the browser window when done.")

            try:
                while True:
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                pass
            finally:
                await browser.close()
                print("\nSession saved. You can now run the API server.")

    asyncio.run(_run())


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="youtube-ask-proxy",
        description="OpenAI-compatible API proxy for YouTube Ask feature",
    )
    subparsers = parser.add_subparsers(dest="command")

    # Serve command (default)
    serve_parser = subparsers.add_parser("serve", help="Run the API server")
    serve_parser.add_argument("--host", default=settings.api_host, help="Host to bind")
    serve_parser.add_argument("--port", type=int, default=settings.api_port, help="Port to bind")
    serve_parser.add_argument(
        "--workers", type=int, default=settings.api_workers, help="Number of workers"
    )

    # Auth bootstrap command
    subparsers.add_parser("auth", help="Bootstrap YouTube/Google authentication")

    args = parser.parse_args()

    if args.command == "auth":
        _bootstrap_auth()
        return 0

    # Default to serve
    configure_logging()
    logger.info(
        "Starting YouTube Ask Proxy API",
        host=getattr(args, "host", settings.api_host),
        port=getattr(args, "port", settings.api_port),
    )

    import uvicorn

    uvicorn.run(
        "youtube_ask_proxy.api:app",
        host=getattr(args, "host", settings.api_host),
        port=getattr(args, "port", settings.api_port),
        workers=getattr(args, "workers", settings.api_workers),
        log_level=settings.log_level.lower(),
        reload=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
