"""Clipboard backends.

Each backend is a thin wrapper around a command-line tool and exposes
``read_text() -> str | None``:

* a ``str`` when the clipboard currently offers text,
* ``None`` when it is empty, holds only non-text data (an image, files) or holds
  a secret flagged by a password manager,
* ``ClipboardError`` when the tool itself fails (no display, timeout, crash).

Display-server specifics live here only; add a new backend class and register it
in ``create_clipboard`` to support another environment.
"""

from __future__ import annotations

import subprocess
from typing import Callable, Sequence

from .platform_env import DesktopSession, which

# Preferred text formats, best first. "STRING" is Latin-1 per the X11 ICCCM.
TEXT_TARGETS = (
    "UTF8_STRING",
    "text/plain;charset=utf-8",
    "text/plain;charset=UTF-8",
    "text/plain",
    "STRING",
    "TEXT",
)
# Password managers (KeePassXC, KDE) mark secrets with this target; never record them.
SECRET_HINT_TARGET = "x-kde-passwordManagerHint"

Runner = Callable[..., "subprocess.CompletedProcess[bytes]"]


class ClipboardError(Exception):
    """Reading the clipboard failed."""


class ClipboardUnavailable(ClipboardError):
    """No usable clipboard backend exists in this environment."""


class CommandClipboard:
    """Shared logic for tools that can list the offered formats and read one of them."""

    name = "command"
    timeout = 2.0
    # stderr fragments that mean "nothing suitable is on the clipboard" rather than a failure.
    empty_markers: tuple[str, ...] = ()

    def __init__(self, runner: Runner = subprocess.run):
        self._runner = runner

    def list_cmd(self) -> list[str]:
        raise NotImplementedError

    def read_cmd(self, target: str) -> list[str]:
        raise NotImplementedError

    def read_text(self) -> str | None:
        listing = self._call(self.list_cmd())
        if listing is None:
            return None
        targets = [line.strip() for line in listing.decode("utf-8", "replace").splitlines() if line.strip()]
        if SECRET_HINT_TARGET in targets:
            return None
        target = next((t for t in TEXT_TARGETS if t in targets), None)
        if target is None:
            return None
        data = self._call(self.read_cmd(target))
        if data is None:
            return None
        encoding = "latin-1" if target == "STRING" else "utf-8"
        return data.decode(encoding, errors="replace")

    def _call(self, argv: Sequence[str]) -> bytes | None:
        try:
            result = self._runner(list(argv), capture_output=True, timeout=self.timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ClipboardError(f"{argv[0]} did not answer within {self.timeout:g}s") from exc
        except OSError as exc:
            raise ClipboardError(f"cannot run {argv[0]}: {exc}") from exc
        if result.returncode == 0:
            return result.stdout
        message = result.stderr.decode("utf-8", "replace").strip()
        if any(marker in message for marker in self.empty_markers):
            return None
        raise ClipboardError(message or f"{argv[0]} exited with status {result.returncode}")


class XclipClipboard(CommandClipboard):
    """X11 CLIPBOARD selection via ``xclip`` (also works through Xwayland)."""

    name = "xclip"
    empty_markers = ("not available",)

    def list_cmd(self) -> list[str]:
        return ["xclip", "-selection", "clipboard", "-o", "-t", "TARGETS"]

    def read_cmd(self, target: str) -> list[str]:
        return ["xclip", "-selection", "clipboard", "-o", "-t", target]


class WlPasteClipboard(CommandClipboard):
    """Wayland clipboard via ``wl-paste`` from wl-clipboard."""

    name = "wl-paste"
    empty_markers = ("Nothing is copied", "No selection", "No suitable type")

    def list_cmd(self) -> list[str]:
        return ["wl-paste", "--list-types"]

    def read_cmd(self, target: str) -> list[str]:
        return ["wl-paste", "--no-newline", "--type", target]


BACKENDS = {"xclip": XclipClipboard, "wl-paste": WlPasteClipboard}


def create_clipboard(session: DesktopSession, preference: str = "auto") -> CommandClipboard:
    """Pick a clipboard backend for ``session`` or raise ``ClipboardUnavailable`` with fix-it advice."""
    has_xclip = which("xclip") is not None and session.display is not None
    has_wlpaste = which("wl-paste") is not None and session.wayland_display is not None

    if preference == "xclip":
        if which("xclip") is None:
            raise ClipboardUnavailable("xclip is not installed. Install it with: sudo apt install xclip")
        if session.display is None:
            raise ClipboardUnavailable("xclip needs an X11 display, but DISPLAY is not set.")
        return XclipClipboard()
    if preference == "wl-paste":
        if which("wl-paste") is None:
            raise ClipboardUnavailable("wl-paste is not installed. Install it with: sudo apt install wl-clipboard")
        if session.wayland_display is None:
            raise ClipboardUnavailable("wl-paste needs a Wayland session, but WAYLAND_DISPLAY is not set.")
        return WlPasteClipboard()
    if preference != "auto":
        raise ClipboardUnavailable(f"Unknown clipboard backend {preference!r}; use auto, xclip or wl-paste.")

    if session.kind == "x11":
        if has_xclip:
            return XclipClipboard()
        raise ClipboardUnavailable(
            "No clipboard tool found. lazy-anki-language-cards reads the X11 clipboard with xclip:\n"
            "    sudo apt install xclip"
        )

    if session.kind == "wayland":
        # GNOME's compositor does not implement the data-control protocol, so wl-paste
        # can only read the clipboard by briefly taking focus. Mutter mirrors the
        # clipboard to Xwayland, so xclip reads it without side effects.
        if session.is_gnome and has_xclip:
            return XclipClipboard()
        if has_wlpaste:
            return WlPasteClipboard()
        if has_xclip:
            return XclipClipboard()
        raise ClipboardUnavailable(
            "No clipboard tool found for this Wayland session. Install one of:\n"
            "    sudo apt install wl-clipboard   # wlroots compositors, KDE Plasma\n"
            "    sudo apt install xclip          # GNOME (reads the clipboard through Xwayland)"
        )

    raise ClipboardUnavailable("Cannot access the clipboard without a graphical session.")
