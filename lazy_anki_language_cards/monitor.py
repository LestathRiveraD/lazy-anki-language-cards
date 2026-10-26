"""Background clipboard polling loop.

The monitor runs in its own thread and never touches HTTP handling, so slow
browser requests cannot delay a capture. On each new clipboard text it takes
the screenshot first and only then saves the record.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Protocol

from .clipboard import ClipboardError
from .screenshot import ScreenshotError
from .storage import RecordStore, StorageError

log = logging.getLogger(__name__)

IGNORE_SECONDS = 30.0


class Clipboard(Protocol):
    name: str

    def read_text(self) -> str | None: ...


class Capturer(Protocol):
    def capture(self) -> str: ...


class ClipboardMonitor(threading.Thread):
    def __init__(
        self,
        clipboard: Clipboard,
        capturer: Capturer,
        store: RecordStore,
        *,
        interval: float = 0.3,
        on_error: Callable[[str], None] | None = None,
    ):
        super().__init__(name="clipboard-monitor", daemon=True)
        self._clipboard = clipboard
        self._capturer = capturer
        self._store = store
        self._interval = interval
        self._on_error = on_error
        self._stop_event = threading.Event()
        self._paused = False
        # Last clipboard state seen (text, or None for empty/non-text). The first
        # successful read only sets this, so text copied before launch is not recorded.
        self._last: str | None = None
        self._primed = False
        self._last_read_error: str | None = None
        # Texts the lazy-anki-language-cards page is about to copy, mapped to an expiry time.
        self._ignored: dict[str, float] = {}
        self._ignored_lock = threading.Lock()

    # ------------------------------------------------------------------ control

    @property
    def paused(self) -> bool:
        return self._paused

    def set_paused(self, paused: bool) -> None:
        self._paused = bool(paused)
        log.info("Capture %s", "paused" if paused else "resumed")

    def ignore_text(self, text: str) -> None:
        """Do not record ``text`` the next time it appears on the clipboard.

        The web page registers text here before copying it, so copying from the
        page (for example the dataset JSON) never creates a record.
        """
        with self._ignored_lock:
            now = time.monotonic()
            self._ignored = {t: exp for t, exp in self._ignored.items() if exp > now}
            self._ignored[text] = now + IGNORE_SECONDS

    def _consume_ignored(self, text: str) -> bool:
        with self._ignored_lock:
            expiry = self._ignored.pop(text, None)
        return expiry is not None and expiry > time.monotonic()

    def prime(self) -> None:
        """Read the current clipboard so it is not recorded. Raises ``ClipboardError``."""
        self._last = self._clipboard.read_text()
        self._primed = True

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        log.info("Clipboard monitor started (%s, every %.2fs)", self._clipboard.name, self._interval)
        while not self._stop_event.wait(self._interval):
            try:
                self.poll_once()
            except Exception:  # keep monitoring whatever happens
                log.exception("Unexpected error in clipboard monitor")
        log.info("Clipboard monitor stopped")

    # ---------------------------------------------------------------- one poll

    def poll_once(self) -> dict[str, Any] | None:
        """Check the clipboard once; return the new record if one was created."""
        try:
            text = self._clipboard.read_text()
        except ClipboardError as exc:
            message = f"Clipboard read failed: {exc}"
            if message != self._last_read_error:
                self._last_read_error = message
                self._report(message)
            return None
        self._last_read_error = None

        if not self._primed:
            self._primed = True
            self._last = text
            return None
        if text == self._last:
            return None
        self._last = text
        if text is None or not text.strip() or self._paused:
            return None
        if self._consume_ignored(text):
            log.debug("Ignoring text copied from the lazy-anki-language-cards page")
            return None
        return self._record(text)

    def _record(self, text: str) -> dict[str, Any] | None:
        image_path = None
        try:
            image_path = self._capturer.capture()
        except ScreenshotError as exc:
            self._report(f"Screenshot failed, record saved without image: {exc}")
        except Exception as exc:
            log.exception("Screenshot backend crashed")
            self._report(f"Screenshot failed, record saved without image: {exc}")

        try:
            record = self._store.add(text, image_path)
        except StorageError as exc:
            self._report(f"Could not save the copied text: {exc}")
            return None
        log.info("Captured %d characters%s", len(text), f" -> {image_path}" if image_path else "")
        return record

    def _report(self, message: str) -> None:
        log.warning("%s", message)
        if self._on_error is not None:
            self._on_error(message)
