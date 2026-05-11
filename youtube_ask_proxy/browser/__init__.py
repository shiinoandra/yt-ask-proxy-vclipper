"""Browser automation controller using Playwright."""

import asyncio
import time
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    Playwright,
    async_playwright,
)

from youtube_ask_proxy.auth import AuthManager
from youtube_ask_proxy.config import settings
from youtube_ask_proxy.logging import get_logger
from youtube_ask_proxy.parsers import ParseError, ResponseParser
from youtube_ask_proxy.stealth import apply_stealth_to_page, get_stealth_args
from youtube_ask_proxy.utils import (
    clean_extracted_text,
    humanized_delay,
    truncate_string,
    with_retry,
)

logger = get_logger(__name__)


class BrowserAutomationError(Exception):
    """Base exception for browser automation failures."""

    pass


class AskFeatureNotFoundError(BrowserAutomationError):
    """Raised when the Ask feature UI is not detected on a page."""

    pass


class AuthenticationRequiredError(BrowserAutomationError):
    """Raised when authentication is required but not available."""

    pass


class ResponseTimeoutError(BrowserAutomationError):
    """Raised when the AI response takes too long."""

    pass


class BrowserController:
    """Manages Playwright browser lifecycle and YouTube Ask interactions."""

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._auth_manager = AuthManager()
        self._parser = ResponseParser()
        self._closed = True

    async def __aenter__(self) -> "BrowserController":
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.stop()

    async def start(self) -> None:
        """Launch the browser and create a new context/page."""
        if not self._closed:
            logger.debug("Browser already started")
            return

        logger.info(
            "Starting browser automation", browser=settings.browser_type, headless=settings.headless
        )
        self._playwright = await async_playwright().start()

        browser_type = getattr(self._playwright, settings.browser_type)
        launch_options: dict[str, Any] = {
            "headless": settings.headless,
            "slow_mo": settings.slow_mo,
        }

        # Inject stealth flags (critical for Google auth)
        stealth_args = get_stealth_args()
        if stealth_args:
            launch_options.setdefault("args", [])
            launch_options["args"].extend(stealth_args)
            logger.info("Stealth mode enabled", flags=len(stealth_args))

        # Use persistent context if user data dir is configured
        user_data_dir = self._auth_manager.ensure_user_data_dir()

        if user_data_dir:
            logger.info("Using persistent browser profile", path=str(user_data_dir))
            self._context = await browser_type.launch_persistent_context(
                user_data_dir=str(user_data_dir),
                **launch_options,
                viewport={"width": settings.viewport_width, "height": settings.viewport_height},
                locale=settings.locale,
                timezone_id=settings.timezone,
                user_agent=settings.user_agent,
                accept_downloads=True,
            )
            self._browser = self._context.browser
        else:
            self._browser = await browser_type.launch(**launch_options)
            self._context = await self._browser.new_context(
                viewport={"width": settings.viewport_width, "height": settings.viewport_height},
                locale=settings.locale,
                timezone_id=settings.timezone,
                user_agent=settings.user_agent,
                accept_downloads=True,
            )
            await self._auth_manager.apply_cookies_to_context(self._context)

        self._page = await self._context.new_page()
        await apply_stealth_to_page(self._page)
        self._closed = False
        logger.info("Browser started successfully")

    async def stop(self) -> None:
        """Gracefully stop the browser and save session state."""
        if self._closed:
            return

        logger.info("Stopping browser automation")

        try:
            if self._context and not self._auth_manager.has_persistent_profile:
                await self._auth_manager.extract_cookies_from_context(self._context)
        except Exception as e:
            logger.warning("Failed to save session state", error=str(e))

        try:
            if self._page:
                await self._page.close()
        except Exception as e:
            logger.warning("Error closing page", error=str(e))

        try:
            if self._context:
                await self._context.close()
        except Exception as e:
            logger.warning("Error closing context", error=str(e))

        try:
            if self._browser:
                await self._browser.close()
        except Exception as e:
            logger.warning("Error closing browser", error=str(e))

        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception as e:
            logger.warning("Error stopping playwright", error=str(e))

        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        self._closed = True
        logger.info("Browser stopped")

    async def _ensure_page(self) -> Page:
        if self._page is None or self._page.is_closed():
            raise BrowserAutomationError("Browser page is not available")
        return self._page

    async def _capture_failure_state(self, prefix: str) -> None:
        """Capture screenshot and HTML dump on failure for debugging."""
        if not settings.capture_screenshots:
            return

        page = self._page
        if page is None or page.is_closed():
            return

        timestamp = int(time.time())
        settings.screenshot_dir.mkdir(parents=True, exist_ok=True)

        try:
            screenshot_path = settings.screenshot_dir / f"{prefix}_{timestamp}.png"
            await page.screenshot(path=str(screenshot_path), full_page=True)
            logger.info("Captured failure screenshot", path=str(screenshot_path))
        except Exception as e:
            logger.warning("Failed to capture screenshot", error=str(e))

        try:
            html_path = settings.screenshot_dir / f"{prefix}_{timestamp}.html"
            html = await page.content()
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
            logger.info("Captured failure HTML", path=str(html_path))
        except Exception as e:
            logger.warning("Failed to capture HTML", error=str(e))

    # ------------------------------------------------------------------
    # YouTube Ask Feature Interaction
    # ------------------------------------------------------------------

    async def navigate_to_video(self, video_url: str) -> None:
        """Navigate to a YouTube video page and wait for full dynamic load.

        YouTube renders many components dynamically after the initial HTML.
        We wait for networkidle (or a generous settle period) so that
        server-injected components like the Ask button have time to appear.

        Args:
            video_url: Full YouTube video URL.

        Raises:
            BrowserAutomationError: If navigation fails.
        """
        page = await self._ensure_page()
        logger.info("Navigating to video", url=video_url)

        try:
            response = await page.goto(
                video_url,
                wait_until="domcontentloaded",
                timeout=settings.navigation_timeout,
            )
            if response and not response.ok:
                raise BrowserAutomationError(f"HTTP {response.status} when loading {video_url}")

            # Wait for the Polymer/JS app shell to attach
            await page.wait_for_selector(
                "ytd-app", state="attached", timeout=settings.browser_timeout
            )

            # CRITICAL: YouTube injects many components dynamically after initial load.
            # We give the page a generous settle window.  In headless CI we may not get
            # perfect networkidle, so we use a polled strategy.
            settle_deadline = time.time() + (settings.page_settle_timeout / 1000)
            logger.debug(
                "Waiting for page JS to settle",
                timeout_ms=settings.page_settle_timeout,
            )
            try:
                # Attempt networkidle first; if it fails we fall back to time-based settle
                await page.wait_for_load_state("networkidle", timeout=settings.page_settle_timeout)
                logger.debug("Page reached networkidle")
            except Exception:
                # Fallback: wait the remaining settle time
                remaining = settle_deadline - time.time()
                if remaining > 0:
                    await asyncio.sleep(remaining)
                logger.debug("Page settle timeout reached (networkidle not achieved)")

            # Check for sign-in requirement
            if settings.auth_required:
                # Use more specific selectors that only appear when the user is
                # actually NOT signed in.  Broad text="Sign in" matches comment
                # prompts even for authenticated users.
                sign_in_indicators = [
                    'ytd-button-renderer:has-text("Sign in"):has(a[href*="ServiceLogin"])',
                    'ytd-masthead button:has-text("Sign in")',
                    'a[href*="accounts.google.com/ServiceLogin"] yt-formatted-string:has-text("Sign in")',
                ]
                auth_required_detected = False
                for indicator in sign_in_indicators:
                    try:
                        locator = page.locator(indicator).first
                        if await locator.is_visible(timeout=3000):
                            logger.error(
                                "Authentication required but not signed in",
                                matched_selector=indicator,
                            )
                            await self._capture_failure_state("auth_required")
                            auth_required_detected = True
                            break
                    except Exception:
                        continue
                if auth_required_detected:
                    raise AuthenticationRequiredError(
                        "YouTube requires sign-in. "
                        "Please authenticate using: python -m youtube_ask_proxy auth"
                    )

            logger.info("Video page loaded and settled successfully")
        except Exception as e:
            if isinstance(e, AuthenticationRequiredError):
                raise
            await self._capture_failure_state("navigate_error")
            raise BrowserAutomationError(f"Failed to navigate to video: {e}") from e

    # ------------------------------------------------------------------
    # Dynamic element polling helpers
    # ------------------------------------------------------------------

    async def _inspect_element(self, locator: Locator) -> dict[str, Any]:
        """Return diagnostic info about a DOM element for debugging.

        Args:
            locator: Playwright Locator.

        Returns:
            Dictionary with tag, class, id, text, bounding box, etc.
        """
        try:
            info = await locator.evaluate(
                """el => {
                    const rect = el.getBoundingClientRect();
                    return {
                        tag: el.tagName,
                        id: el.id || null,
                        class: el.className || null,
                        text: el.innerText?.substring(0, 100) || null,
                        ariaLabel: el.getAttribute('aria-label') || null,
                        role: el.getAttribute('role') || null,
                        disabled: el.disabled || false,
                        hidden: el.hidden || false,
                        shadowRoot: !!el.shadowRoot,
                        parentTag: el.parentElement?.tagName || null,
                        rect: {
                            x: rect.x,
                            y: rect.y,
                            width: rect.width,
                            height: rect.height,
                            top: rect.top,
                            left: rect.left,
                        },
                    };
                }"""
            )
            return info
        except Exception as e:
            return {"error": str(e)}

    async def _poll_for_locator(
        self,
        page: Page,
        selectors: list[str],
        timeout_ms: int,
        description: str,
        use_last: bool = False,
        require_visible: bool = True,
    ) -> Locator | None:
        """Poll multiple selectors until one matches or timeout.

        YouTube injects components dynamically after page load.  A single
        static scan often misses them.  This method polls continuously.

        Args:
            page: Playwright Page.
            selectors: CSS selector candidates.
            timeout_ms: Maximum polling duration.
            description: Human-readable description for logging.
            use_last: Whether to use `.last` instead of `.first`.
            require_visible: Whether the element must be visible.

        Returns:
            A matching Locator, or None if timeout expires.
        """
        deadline = time.time() + (timeout_ms / 1000)
        attempt = 0

        while time.time() < deadline:
            attempt += 1
            for selector in selectors:
                try:
                    locator = (
                        page.locator(selector).last if use_last else page.locator(selector).first
                    )
                    count = await locator.count()
                    if count == 0:
                        continue
                    if require_visible:
                        # Short visibility check — if not visible yet, keep polling
                        visible = await locator.is_visible(timeout=1000)
                        if not visible:
                            continue
                    info = await self._inspect_element(locator)
                    logger.info(
                        f"Found {description}",
                        selector=selector,
                        attempt=attempt,
                        tag=info.get("tag"),
                        element_id=info.get("id"),
                        class_name=info.get("class"),
                        text_preview=truncate_string(info.get("text") or "", 80),
                        has_shadow_root=info.get("shadowRoot"),
                        disabled=info.get("disabled"),
                        rect=info.get("rect"),
                    )
                    return locator
                except Exception:
                    continue
            await asyncio.sleep(settings.ask_poll_interval)

        logger.warning(
            f"Timeout polling for {description}",
            selectors_tried=len(selectors),
            timeout_ms=timeout_ms,
        )
        return None

    async def _wait_for_ask_button(self, page: Page) -> Locator | None:
        """Poll for the Ask button to appear dynamically.

        Returns:
            Playwright Locator if found, None otherwise.
        """
        selectors = [
            # Exact aria-label match (avoids matching CC/subtitle "AI" buttons)
            'button[aria-label="Ask"]',
            # Modern YouTube button structure (observed in the wild)
            'button.ytSpecButtonShapeNextHost:has(.ytSpecButtonShapeNextButtonTextContent:has-text("Ask"))',
            'yt-button-view-model:has-text("Ask") button',
            '.you-chat-entrypoint-button button',
            # Data-testid / aria semantic selectors
            'button[data-testid="ask-button"]',
            # Text content selectors
            'button:has-text("Ask")',
            'yt-button-shape:has-text("Ask")',
            # Class-based heuristic selectors
            'ytd-button-renderer:has-text("Ask")',
            'ytd-compact-link-renderer:has-text("Ask")',
            # Experimental / newer UI
            'tp-yt-paper-button:has-text("Ask")',
            '[role="button"]:has-text("Ask")',
            # Engagement panel variants
            'ytd-engagement-panel-section-list-renderer:has-text("Ask")',
            'ytd-engagement-panel-title-header-renderer:has-text("Ask")',
        ]
        return await self._poll_for_locator(
            page,
            selectors,
            settings.ask_feature_detection_timeout,
            "Ask button",
        )

    async def _wait_for_ask_input(self, page: Page) -> Locator | None:
        """Poll for the Ask text input field to appear dynamically.

        Returns:
            Playwright Locator if found, None otherwise.
        """
        selectors = [
            'input[placeholder*="Ask" i]',
            'textarea[placeholder*="Ask" i]',
            'input[aria-label*="Ask" i]',
            'textarea[aria-label*="Ask" i]',
            'input[data-testid="ask-input"]',
            'textarea[data-testid="ask-input"]',
            "input#input",
            "textarea#input",
            '[contenteditable="true"]',
            'yt-formatted-string[placeholder*="Ask" i]',
            # Engagement panel specific inputs
            "ytd-engagement-panel-section-list-renderer input",
            "ytd-engagement-panel-section-list-renderer textarea",
        ]
        return await self._poll_for_locator(
            page,
            selectors,
            settings.ask_panel_open_timeout,
            "Ask input",
        )

    async def _wait_for_ask_submit(self, page: Page) -> Locator | None:
        """Poll for the Ask submit button to appear dynamically.

        Returns:
            Playwright Locator if found, None otherwise.
        """
        selectors = [
            'button[type="submit"]',
            'button[aria-label*="Send" i]',
            'button[aria-label*="Submit" i]',
            'button:has-text("Send")',
            'button:has-text("Submit")',
            'yt-button-renderer:has-text("Send")',
            'yt-icon-button:has(yt-icon[icon="send"])',
            '[role="button"][aria-label*="Send" i]',
            # Panel-specific submit buttons
            'ytd-engagement-panel-section-list-renderer button:has-text("Send")',
        ]
        return await self._poll_for_locator(
            page,
            selectors,
            settings.ask_panel_open_timeout,
            "Ask submit button",
        )

    async def _wait_for_response_container(self, page: Page) -> Locator | None:
        """Poll for the container where the AI response appears.

        YouTube injects multiple chat items with class ``ytwYouChatItemViewModelHost``
        (welcome messages, suggested chips).  The actual AI response has the
        additional ``ytwYouChatItemViewModelColumnLayout`` class and contains a
        ``markdown-div``.  Suggested-question chips share the same
        ``data-target-id`` prefix but do *not* have ``ColumnLayout``.  We therefore
        prioritise selectors that require both ``ColumnLayout`` and
        ``data-target-id^="youchat-"``.

        Returns:
            Playwright Locator if found, None otherwise.
        """
        selectors = [
            # 1. AI response item with ColumnLayout + thumbs buttons (fully rendered).
            'you-chat-item-view-model.ytwYouChatItemViewModelColumnLayout[data-target-id^="youchat-"]:has(.ytwThumbsUpDownThumbs) div.ytwYouChatItemViewModelHost',
            # 2. AI response item with ColumnLayout but thumbs not yet injected.
            'you-chat-item-view-model.ytwYouChatItemViewModelColumnLayout[data-target-id^="youchat-"] div.ytwYouChatItemViewModelHost',
            # 3. ColumnLayout wrapper itself (fallback if inner div is missing).
            'you-chat-item-view-model.ytwYouChatItemViewModelColumnLayout[data-target-id^="youchat-"]',
            # 4. markdown-div ONLY when nested inside a real youchat item
            #    (standalone markdown-div matches welcome messages too early).
            'you-chat-item-view-model[data-target-id^="youchat-"] markdown-div.ytwMarkdownDivHost',
            'you-chat-item-view-model[data-target-id^="youchat-"] .ytwYouChatItemViewModelHost',
            # 5. Any response host that already has thumbs buttons.
            '.ytwYouChatItemViewModelHost:has(.ytwThumbsUpDownThumbs)',
            # Fallbacks: older / generic selectors.
            '[data-testid="ask-response"]',
            '[data-testid="ai-response"]',
            ".ytd-ask-response-renderer",
            ".ytd-generative-ai-summary-body-renderer",
            '[role="log"]',
            'ytd-comment-renderer:has-text("AI")',
            'div[role="listitem"]',
            'ytd-engagement-panel-section-list-renderer div[role="log"]',
            "ytd-engagement-panel-section-list-renderer ytd-comment-renderer",
        ]
        return await self._poll_for_locator(
            page,
            selectors,
            settings.response_timeout,
            "response container",
            use_last=True,
            require_visible=False,
        )

    async def _wait_for_response_text(self, page: Page, timeout_ms: int | None = None) -> str:
        """Wait for and extract the final AI response text.

        This method first waits for the response container to appear in the DOM
        (handling dynamic injection), then polls until the text stabilises or
        the thumbs up/down buttons appear (strong signal that rendering is done).

        Args:
            page: Playwright Page.
            timeout_ms: Maximum time to wait in milliseconds.

        Returns:
            Extracted response text.

        Raises:
            ResponseTimeoutError: If response is not ready within timeout.
        """
        timeout = timeout_ms or settings.response_timeout
        poll_interval = 1.0  # seconds
        start_time = time.time()
        last_text = ""
        stable_count = 0
        required_stable_polls = 3  # Text must be stable for 3 consecutive polls

        logger.info("Waiting for AI response container to appear", timeout_ms=timeout)

        # PHASE 1: Wait for the response container to be injected into the DOM.
        # YouTube may create the response node only after the LLM starts streaming.
        container = await self._wait_for_response_container(page)
        if container is None:
            await self._capture_failure_state("response_container_not_found")
            raise ResponseTimeoutError("Response container never appeared in the DOM")

        logger.info("Response container detected, polling for text stabilisation")

        # PHASE 2: Poll the container until text stops changing or completion
        # indicators (thumbs up/down buttons) appear.
        while (time.time() - start_time) * 1000 < timeout:
            current_text = ""

            try:
                text = await container.text_content()
                current_text = clean_extracted_text(text or "")
            except Exception:
                pass

            # Fallback: broader selectors if the primary container is empty
            if not current_text:
                fallback_selectors = [
                    'div[role="log"] div',
                    "ytd-comment-renderer #content-text",
                    "div#content",
                    'ytd-engagement-panel-section-list-renderer div[role="log"]',
                    "markdown-div.ytwMarkdownDivHost p",
                    "div.ytwYouChatItemViewModelHost markdown-div",
                ]
                for fs in fallback_selectors:
                    try:
                        locator = page.locator(fs).last
                        if await locator.count() > 0:
                            text = await locator.text_content()
                            candidate = clean_extracted_text(text or "")
                            if len(candidate) > len(current_text):
                                current_text = candidate
                    except Exception:
                        continue

            # Check for completion indicators: thumbs up/down buttons mean
            # the response has fully rendered.
            response_complete = False
            try:
                thumbs_locator = container.locator(
                    '.ytwThumbsUpDownThumbs, button[aria-label="Thumbs up"], button[aria-label="Thumbs down"]'
                )
                if await thumbs_locator.count() > 0:
                    visible = await thumbs_locator.first.is_visible(timeout=500)
                    if visible:
                        response_complete = True
                        logger.info("Response completion detected (thumbs buttons visible)")
            except Exception:
                pass

            # If we have text and it's either stable or the thumbs buttons are
            # visible, consider the response done.
            has_meaningful_text = current_text and len(current_text) > 10

            if has_meaningful_text:
                if response_complete:
                    logger.info(
                        "Response finished (thumbs buttons present)",
                        length=len(current_text),
                        duration_ms=int((time.time() - start_time) * 1000),
                    )
                    return current_text

                if current_text == last_text:
                    stable_count += 1
                    if stable_count >= required_stable_polls:
                        logger.info(
                            "Response text stabilized",
                            length=len(current_text),
                            duration_ms=int((time.time() - start_time) * 1000),
                        )
                        return current_text
                else:
                    stable_count = 0

            last_text = current_text
            await asyncio.sleep(poll_interval)

        # If we have some text but timed out, return what we have
        if last_text:
            logger.warning("Response timeout, returning partial text", length=len(last_text))
            return last_text

        await self._capture_failure_state("response_timeout")
        raise ResponseTimeoutError(f"AI response not received within {timeout}ms")

    # ------------------------------------------------------------------
    # Anti-bot interaction helpers
    # ------------------------------------------------------------------

    async def _human_click(
        self,
        locator: Locator,
        description: str = "element",
    ) -> None:
        """Click an element using multiple strategies, preferring coordinate-based.

        YouTube's Web Components (shadow DOM) and custom event gates mean that
        synthetic DOM events (``dispatchEvent``, ``el.click()``) are frequently
        ignored.  The most reliable approach is to move the **real OS mouse**
        to the element's on-screen coordinates and click there.

        Strategies (tried in order):

        1. **Coordinate click** — Get ``bounding_box()``, move real mouse,
           click at (x + width/2, y + height/2).  This bypasses shadow-DOM
           boundaries and element-specific event listeners entirely.
        2. **Playwright ``click()``** with ``force=True``.
        3. **Click a child ``<button>`` or ``<a>``** inside the element.
        4. **JS ``el.click()``** directly on the element.
        5. **PointerEvent dispatch** — ``pointerdown`` + ``pointerup`` sequence.

        Args:
            locator: Playwright Locator to click.
            description: Human-readable name for logging.
        """
        page = locator.page
        errors: list[str] = []

        # Strategy 1: Coordinate-based click (most reliable for shadow DOM)
        try:
            box = await locator.bounding_box()
            if box and box["width"] > 0 and box["height"] > 0:
                target_x = box["x"] + box["width"] / 2
                target_y = box["y"] + box["height"] / 2
                logger.debug(
                    f"Coordinate click on {description}",
                    x=target_x,
                    y=target_y,
                    width=box["width"],
                    height=box["height"],
                )
                # Move mouse to element with slight randomisation
                await page.mouse.move(target_x, target_y)
                humanized_delay(50, 200)
                # Real OS-level click (left button, single click)
                await page.mouse.down()
                humanized_delay(30, 120)
                await page.mouse.up()
                logger.info(f"Clicked {description} via coordinate mouse")
                return
            else:
                errors.append("bounding_box empty or zero-size")
        except Exception as e:
            errors.append(f"coordinate_click: {e}")

        # Strategy 2: Standard Playwright click with force
        try:
            await locator.click(force=True)
            logger.info(f"Clicked {description} via force click")
            return
        except Exception as e:
            errors.append(f"force_click: {e}")

        # Strategy 3: Click deepest interactive child element
        try:
            child = locator.locator("button, a, [role='button']").first
            if await child.count() > 0:
                await child.click(force=True)
                logger.info(f"Clicked {description} via child element")
                return
        except Exception as e:
            errors.append(f"child_click: {e}")

        # Strategy 4: Direct JS el.click()
        try:
            await locator.evaluate("el => el.click()")
            logger.info(f"Clicked {description} via JS el.click()")
            return
        except Exception as e:
            errors.append(f"js_click: {e}")

        # Strategy 5: PointerEvent sequence
        try:
            await locator.evaluate(
                """el => {
                    const rect = el.getBoundingClientRect();
                    const x = rect.left + rect.width / 2;
                    const y = rect.top + rect.height / 2;
                    const opts = { bubbles: true, cancelable: true, clientX: x, clientY: y };
                    el.dispatchEvent(new PointerEvent('pointerdown', opts));
                    el.dispatchEvent(new PointerEvent('pointerup', opts));
                    el.dispatchEvent(new MouseEvent('click', opts));
                }"""
            )
            logger.info(f"Clicked {description} via pointer event sequence")
            return
        except Exception as e:
            errors.append(f"pointer_event: {e}")

        logger.error(
            f"All click strategies failed for {description}",
            errors=errors,
        )
        raise BrowserAutomationError(
            f"Could not click {description}. Tried: {', '.join(errors)}"
        )

    async def _human_type(
        self,
        locator: Locator,
        text: str,
        description: str = "input",
    ) -> None:
        """Type text into an element quickly but with enough events to trigger
        framework listeners (React / Polymer).

        ``press_sequentially`` with per-character delay is too slow for long
        prompts (> 3 000 chars) and often gets truncated by timeouts.  This
        helper uses ``fill()`` for speed then manually fires the events that
        YouTube's components need to enable the submit button.

        Args:
            locator: Playwright Locator to type into.
            text: Text to type.
            description: Human-readable name for logging.
        """
        try:
            await locator.scroll_into_view_if_needed()
            await locator.focus()
            humanized_delay(100, 300)

            # Fast fill — sets value in one shot
            await locator.fill(text)
            humanized_delay(100, 300)

            # Fire the event sequence frameworks expect
            await locator.evaluate(
                """el => {
                    const opts = { bubbles: true, cancelable: true, composed: true };
                    el.dispatchEvent(new Event('focus', opts));
                    el.dispatchEvent(new Event('keydown', opts));
                    el.dispatchEvent(new Event('keypress', opts));
                    el.dispatchEvent(new Event('input', opts));
                    el.dispatchEvent(new Event('keyup', opts));
                    el.dispatchEvent(new Event('change', opts));
                }"""
            )
            logger.debug(f"Filled {description}", length=len(text))
        except Exception as e:
            logger.warning(f"Failed to fill {description}", error=str(e))
            raise

    @with_retry(
        exceptions=(BrowserAutomationError, ResponseTimeoutError, ParseError),
    )
    async def ask(
        self,
        video_url: str,
        prompt: str,
    ) -> dict[str, Any]:
        """Submit a prompt to YouTube Ask for a given video and return the response.

        This is the main high-level method that orchestrates the entire flow:
        1. Navigate to video
        2. Open Ask UI
        3. Submit prompt
        4. Wait for and extract response
        5. Parse and return structured JSON

        Args:
            video_url: YouTube video URL.
            prompt: The prompt text to submit.

        Returns:
            Parsed response dictionary.

        Raises:
            BrowserAutomationError: If any step fails after retries.
        """
        page = await self._ensure_page()

        # Step 1: Navigate
        await self.navigate_to_video(video_url)
        humanized_delay(200, 600)

        # Step 2: Detect and open Ask feature (with dynamic rendering polling)
        ask_button = await self._wait_for_ask_button(page)
        if ask_button is None:
            logger.error("Ask feature not found on page after polling")
            await self._capture_failure_state("ask_not_found")
            raise AskFeatureNotFoundError(
                "Could not detect the YouTube Ask feature on this video. "
                "It may not be available for this video, the UI has changed, "
                "or the component had not been injected by the time we stopped polling."
            )

        logger.info("Clicking Ask button")
        await self._human_click(ask_button, description="Ask button")
        humanized_delay(500, 1200)

        # Step 3: Wait for panel to open and find input (dynamic rendering)
        ask_input = await self._wait_for_ask_input(page)
        if ask_input is None:
            logger.error("Ask input field not found after panel open timeout")
            await self._capture_failure_state("ask_input_not_found")
            raise BrowserAutomationError(
                "Could not find the Ask input field. The panel may not have opened."
            )

        logger.info("Typing prompt", prompt_preview=truncate_string(prompt, 100))
        await self._human_type(ask_input, prompt, description="Ask input")
        humanized_delay(300, 700)

        # Step 4: Submit
        submit_button = await self._wait_for_ask_submit(page)
        if submit_button is None:
            # Try pressing Enter as fallback
            logger.warning("Submit button not found, trying Enter key")
            await ask_input.press("Enter")
        else:
            await self._human_click(submit_button, description="submit button")

        logger.info("Prompt submitted, waiting for response")
        humanized_delay(500, 1000)

        # Step 5: Wait for and extract response
        raw_response = await self._wait_for_response_text(page)
        logger.info(
            "Response extracted",
            length=len(raw_response),
            preview=truncate_string(raw_response, 200),
        )

        # Step 6: Parse
        try:
            parsed = self._parser.parse(raw_response)
            return parsed
        except ParseError as e:
            logger.error("Failed to parse response", error=str(e))
            raise BrowserAutomationError(f"Failed to parse AI response: {e}") from e
