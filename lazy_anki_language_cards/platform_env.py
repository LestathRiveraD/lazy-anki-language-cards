"""Desktop session detection.

Every display-server specific decision (which clipboard tool, which screenshot
tool) starts from the ``DesktopSession`` returned by ``detect_session``.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Mapping

NO_SESSION_HELP = (
    "No graphical desktop session detected (neither DISPLAY nor WAYLAND_DISPLAY is set).\n"
    "Run lazy-anki-language-cards from a terminal inside your desktop session. If you start it from\n"
    "SSH, a systemd unit or cron, export the session variables first, for example:\n"
    "    export DISPLAY=:0            # X11\n"
    "    export WAYLAND_DISPLAY=wayland-0 XDG_SESSION_TYPE=wayland   # Wayland"
)


@dataclass(frozen=True)
class DesktopSession:
    kind: str  # "x11", "wayland" or "unknown"
    desktop: str
    display: str | None
    wayland_display: str | None

    @property
    def is_gnome(self) -> bool:
        return "gnome" in self.desktop.lower()

    @property
    def is_kde(self) -> bool:
        return "kde" in self.desktop.lower()

    def describe(self) -> str:
        desktop = self.desktop or "unknown desktop"
        return f"{self.kind} session ({desktop})"


def detect_session(environ: Mapping[str, str] | None = None) -> DesktopSession:
    env = os.environ if environ is None else environ
    session_type = env.get("XDG_SESSION_TYPE", "").strip().lower()
    display = env.get("DISPLAY") or None
    wayland_display = env.get("WAYLAND_DISPLAY") or None

    if session_type == "wayland" or (session_type != "x11" and wayland_display):
        kind = "wayland"
    elif display:
        # Covers XDG_SESSION_TYPE=x11 as well as "tty" sessions with a forwarded DISPLAY.
        kind = "x11"
    else:
        kind = "unknown"

    desktop = env.get("XDG_CURRENT_DESKTOP") or env.get("DESKTOP_SESSION") or ""
    return DesktopSession(kind=kind, desktop=desktop, display=display, wayland_display=wayland_display)


def which(name: str) -> str | None:
    return shutil.which(name)
