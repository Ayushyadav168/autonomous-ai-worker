"""
Thin Playwright wrapper exposing a small, stable action surface to the agent.

Key design choice: read_page() assigns a `data-agent-ref` attribute to every
interactive/informative element and returns a compact text snapshot keyed by
ref id (ref_1, ref_2, ...), instead of handing the LLM raw HTML or asking it
to write CSS selectors. This is far more reliable for an LLM driving a
browser: it reasons over short stable handles instead of guessing selectors
that may not exist or may match multiple elements.
"""
import base64
import os
import time

from playwright.sync_api import sync_playwright

_SNAPSHOT_JS = """
() => {
  const SELECTOR = 'a, button, input, textarea, select, img, tr[data-invoice-number], [role]';
  const nodes = Array.from(document.querySelectorAll(SELECTOR));
  let refCounter = 0;
  const out = [];
  for (const el of nodes) {
    const rect = el.getBoundingClientRect();
    const visible = rect.width > 0 && rect.height > 0 &&
      window.getComputedStyle(el).visibility !== 'hidden';
    if (!visible) continue;
    refCounter += 1;
    const ref = 'ref_' + refCounter;
    el.setAttribute('data-agent-ref', ref);
    const tag = el.tagName.toLowerCase();
    const item = {
      ref,
      tag,
      id: el.id || null,
      text: (el.innerText || el.value || el.alt || '').replace(/\\s+/g, ' ').trim().slice(0, 160),
    };
    if (tag === 'a') item.href = el.getAttribute('href');
    if (tag === 'img') item.src = el.getAttribute('src');
    if (tag === 'input' || tag === 'textarea') {
      item.input_type = el.getAttribute('type') || 'text';
      item.name = el.getAttribute('name');
      item.placeholder = el.getAttribute('placeholder');
      item.value = el.value;
    }
    if (tag === 'tr') {
      item.data = {
        invoice_number: el.getAttribute('data-invoice-number'),
        company: el.getAttribute('data-company'),
      };
    }
    out.push(item);
  }
  return out;
}
"""


class BrowserError(Exception):
    """Raised for any browser-action failure; callers surface this to the LLM as an observation."""


class BrowserController:
    def __init__(self, headless: bool = True, screenshot_dir: str = "logs/screenshots"):
        self.headless = headless
        self.screenshot_dir = screenshot_dir
        os.makedirs(screenshot_dir, exist_ok=True)
        self._pw = None
        self.browser = None
        self.page = None

    def start(self):
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(headless=self.headless)
        self.page = self.browser.new_page(viewport={"width": 1280, "height": 900})

    def stop(self):
        try:
            if self.browser:
                self.browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    def navigate(self, url: str):
        try:
            self.page.goto(url, wait_until="load", timeout=10000)
        except Exception as e:
            raise BrowserError(f"Failed to navigate to {url}: {e}") from e
        return f"Navigated to {self.page.url}"

    def read_page(self) -> dict:
        elements = self.page.evaluate(_SNAPSHOT_JS)
        try:
            body_text = self.page.inner_text("body")
        except Exception:
            body_text = ""
        return {
            "url": self.page.url,
            "title": self.page.title(),
            "elements": elements,
            # Full visible text, not just interactive elements - catches error
            # banners, headings, and any other plain-text content that isn't
            # a link/button/input (e.g. a <div class="error">...</div>).
            "visible_text": " ".join(body_text.split()),
        }

    def click(self, ref: str):
        locator = self.page.locator(f'[data-agent-ref="{ref}"]')
        if locator.count() == 0:
            raise BrowserError(
                f"No element with ref={ref} found. Call read_page again - "
                "refs are only valid for the current page state."
            )
        try:
            locator.first.click(timeout=5000)
            self.page.wait_for_load_state("load", timeout=5000)
        except Exception as e:
            raise BrowserError(f"Failed to click ref={ref}: {e}") from e
        return f"Clicked {ref}; now at {self.page.url}"

    def type_text(self, ref: str, text: str, clear_first: bool = True):
        locator = self.page.locator(f'[data-agent-ref="{ref}"]')
        if locator.count() == 0:
            raise BrowserError(
                f"No element with ref={ref} found. Call read_page again - "
                "refs are only valid for the current page state."
            )
        try:
            if clear_first:
                locator.first.fill("")
            locator.first.fill(text)
        except Exception as e:
            raise BrowserError(f"Failed to type into ref={ref}: {e}") from e
        return f"Typed into {ref}"

    def screenshot(self, label: str = "step") -> str:
        ts = int(time.time() * 1000)
        path = os.path.join(self.screenshot_dir, f"{ts}_{label}.png")
        self.page.screenshot(path=path)
        return path

    def view_image(self, ref: str = None, url: str = None) -> bytes:
        """Returns raw PNG bytes of either an on-page <img ref> or an arbitrary URL, for vision tool results."""
        if ref:
            locator = self.page.locator(f'[data-agent-ref="{ref}"]')
            if locator.count() == 0:
                raise BrowserError(f"No element with ref={ref} found.")
            return locator.first.screenshot()
        if url:
            resp = self.page.request.get(url)
            if not resp.ok:
                raise BrowserError(f"Failed to fetch image {url}: HTTP {resp.status}")
            return resp.body()
        raise BrowserError("view_image requires either ref or url")


def image_bytes_to_base64(data: bytes) -> str:
    return base64.b64encode(data).decode("utf-8")
