"""Stealth / anti-detection helpers for Playwright.

Google (and other sites) detect automation via several vectors:

* The ``--enable-automation`` flag Chromium adds by default
* ``navigator.webdriver === true``
* Missing ``window.chrome`` runtime object
* Zero plugins in ``navigator.plugins``
* The ``Permissions.prototype.query`` leak
* Blink feature ``AutomationControlled``

This module provides launch arguments and runtime scripts that patch or
hide these signatures so that the browser fingerprint looks closer to a
regular Chrome install.
"""

from typing import Any

from youtube_ask_proxy.config import settings
from youtube_ask_proxy.logging import get_logger

logger = get_logger(__name__)

# Chromium command-line flags that reduce automation fingerprints.
_STEALTH_ARGS: list[str] = [
    # Disable the Blink automation flag — this is the single most important
    # flag for avoiding "browser might not be secure" on Google sign-in.
    "--disable-blink-features=AutomationControlled",
    # Disable the ``AutomationControlled`` feature itself (redundant but safe).
    "--disable-features=IsolateOrigins,site-per-process",
    # Don't show the "Chrome is being controlled by automated test software"
    # infobar.  Note: Playwright already suppresses the infobar UI, but the
    # underlying flag still leaks; the Blink flag above handles the real leak.
    "--disable-infobars",
    # Reduce miscellaneous background noise that differs from a normal user.
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-breakpad",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-dev-shm-usage",
    "--disable-extensions",
    "--disable-features=TranslateUI",
    "--disable-hang-monitor",
    "--disable-ipc-flooding-protection",
    "--disable-popup-blocking",
    "--disable-prompt-on-repost",
    "--disable-renderer-backgrounding",
    "--disable-sync",
    "--force-color-profile=srgb",
    "--metrics-recording-only",
    "--no-first-run",
    "--password-store=basic",
    "--use-mock-keychain",
]

# JavaScript that is injected into every page before any site code runs.
_STEALTH_INIT_SCRIPT = """
// Remove the webdriver property
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
});

// Pretend we have a normal Chrome runtime
window.chrome = window.chrome || {};
window.chrome.runtime = window.chrome.runtime || {};

// Add plausible plugins (many sites check length > 0)
const mockPlugins = [
    {
        name: "Chrome PDF Plugin",
        filename: "internal-pdf-viewer2",
        description: "Portable Document Format",
        version: undefined,
        length: 1,
        item: () => { return {type: "application/x-google-chrome-pdf", suffixes: "pdf", description: "Portable Document Format"}; },
        namedItem: () => { return {type: "application/x-google-chrome-pdf", suffixes: "pdf", description: "Portable Document Format"}; },
    },
    {
        name: "Native Client",
        filename: "native-client.dll",
        description: "Native Client module",
        version: undefined,
        length: 2,
        item: (idx) => { return {type: idx === 0 ? "application/x-nacl" : "application/x-pnacl", suffixes: "", description: "Native Client module"}; },
        namedItem: (name) => { return {type: name, suffixes: "", description: "Native Client module"}; },
    },
];
Object.defineProperty(navigator, 'plugins', {
    get: () => mockPlugins,
});

// Languages
Object.defineProperty(navigator, 'languages', {
    get: () => ['en-US', 'en'],
});

// Permissions query patch — prevents sites from detecting that "notifications"
// permission was denied by automation defaults.
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery(parameters)
);

// Device memory / hardware concurrency — set to plausible desktop values
Object.defineProperty(navigator, 'deviceMemory', {
    get: () => 8,
});
Object.defineProperty(navigator, 'hardwareConcurrency', {
    get: () => 8,
});
"""


def get_stealth_args() -> list[str]:
    """Return Chromium args that reduce automation detection.

    Returns:
        List of CLI flags.
    """
    if not settings.stealth_enabled:
        return []
    return list(_STEALTH_ARGS)


def get_stealth_init_script() -> str:
    """Return the JS init script that patches automation leaks.

    Returns:
        JavaScript source string.
    """
    if not settings.stealth_enabled:
        return ""
    return _STEALTH_INIT_SCRIPT


async def apply_stealth_to_page(page: Any) -> None:
    """Add the stealth init script to a page.

    Args:
        page: Playwright Page instance.
    """
    script = get_stealth_init_script()
    if not script:
        return
    try:
        await page.add_init_script(script)
        logger.debug("Applied stealth init script to page")
    except Exception as e:
        logger.warning("Failed to apply stealth init script", error=str(e))
