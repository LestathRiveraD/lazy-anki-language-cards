"""Fullscreen screenshot capture.

A ``ScreenshotCapturer`` owns the screenshots directory and file naming; the
actual pixels come from one of several backends, chosen per display server by
``build_screenshot_backends``. Backends only have to write a PNG to a given path.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol, Sequence

from .platform_env import DesktopSession, which

log = logging.getLogger(__name__)

SCREENSHOTS_DIRNAME = "screenshots"
# Names produced by ScreenshotCapturer; only these are ever deleted.
SCREENSHOT_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}_\d{3}(-\d+)?\.png$")


class ScreenshotError(Exception):
    """Capturing a screenshot failed."""


class ScreenshotUnavailable(ScreenshotError):
    """No screenshot backend can work in this environment."""


class ScreenshotBackend(Protocol):
    name: str

    def grab_png(self, path: Path) -> None:
        """Write a PNG of the whole desktop to ``path`` or raise."""


class MssBackend:
    """X11 capture through the ``mss`` library: fast and in-process."""

    name = "mss"

    def __init__(self) -> None:
        import mss
        import mss.tools

        self._mss = mss

    def grab_png(self, path: Path) -> None:
        # A new instance per capture: mss's X11 handle is bound to the creating thread.
        with self._mss.mss() as sct:
            shot = sct.grab(sct.monitors[0])  # monitor 0 = all monitors combined
        self._mss.tools.to_png(shot.rgb, shot.size, output=str(path))


class CommandBackend:
    """Capture by running an external screenshot tool."""

    timeout = 15.0

    def __init__(self, name: str, build_argv: Callable[[str], list[str]], runner=subprocess.run):
        self.name = name
        self._build_argv = build_argv
        self._runner = runner

    def grab_png(self, path: Path) -> None:
        argv = self._build_argv(str(path))
        try:
            result = self._runner(argv, capture_output=True, timeout=self.timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ScreenshotError(f"{argv[0]} timed out") from exc
        except OSError as exc:
            raise ScreenshotError(f"cannot run {argv[0]}: {exc}") from exc
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", "replace").strip()
            raise ScreenshotError(detail or f"{argv[0]} exited with status {result.returncode}")


COMMAND_TOOLS: dict[str, Callable[[str], list[str]]] = {
    "scrot": lambda p: ["scrot", "--overwrite", p],
    "import": lambda p: ["import", "-silent", "-window", "root", p],
    "gnome-screenshot": lambda p: ["gnome-screenshot", f"--file={p}"],
    "grim": lambda p: ["grim", p],
    "spectacle": lambda p: ["spectacle", "--background", "--nonotify", "--fullscreen", "--output", p],
}
BACKEND_NAMES = ("mss", *COMMAND_TOOLS)

INSTALL_HINTS = {
    "x11": "    python3 -m pip install mss      # recommended\n    sudo apt install scrot          # alternative",
    "wayland": (
        "    sudo apt install gnome-screenshot   # GNOME (may be blocked on recent GNOME releases)\n"
        "    sudo apt install grim               # Sway, Hyprland and other wlroots compositors\n"
        "    sudo apt install kde-spectacle      # KDE Plasma\n"
        "On GNOME, the reliable option is to log in with \"Ubuntu on Xorg\"."
    ),
}


def _make_backend(name: str, session: DesktopSession) -> ScreenshotBackend | None:
    if name == "mss":
        if session.display is None:
            return None
        try:
            return MssBackend()
        except ImportError:
            return None
    if which(name) is None:
        return None
    return CommandBackend(name, COMMAND_TOOLS[name])


def build_screenshot_backends(session: DesktopSession, preference: str = "auto") -> list[ScreenshotBackend]:
    """Return the usable backends for ``session``, most suitable first."""
    if preference != "auto":
        if preference not in BACKEND_NAMES:
            raise ScreenshotUnavailable(
                f"Unknown screenshot backend {preference!r}; choose one of: auto, {', '.join(BACKEND_NAMES)}"
            )
        backend = _make_backend(preference, session)
        if backend is None:
            raise ScreenshotUnavailable(f"Screenshot backend {preference!r} is not installed or cannot run here.")
        return [backend]

    if session.kind == "x11":
        order = ["mss", "scrot", "import", "gnome-screenshot"]
    elif session.kind == "wayland":
        # X11 grabbers only see Xwayland windows on Wayland, so they are not used.
        if session.is_gnome:
            order = ["gnome-screenshot", "grim", "spectacle"]
        elif session.is_kde:
            order = ["spectacle", "grim", "gnome-screenshot"]
        else:
            order = ["grim", "spectacle", "gnome-screenshot"]
    else:
        raise ScreenshotUnavailable("Cannot take screenshots without a graphical session.")

    backends = [b for b in (_make_backend(name, session) for name in order) if b is not None]
    if not backends:
        raise ScreenshotUnavailable(
            f"No screenshot tool available for this {session.kind} session. Install one of:\n"
            + INSTALL_HINTS[session.kind]
        )
    return backends


class ScreenshotCapturer:
    """Takes screenshots into ``<data_dir>/screenshots`` with unique, timestamped names."""

    def __init__(
        self,
        data_dir: Path,
        backends: Sequence[ScreenshotBackend],
        clock: Callable[[], datetime] = datetime.now,
    ):
        if not backends:
            raise ValueError("at least one screenshot backend is required")
        self.data_dir = Path(data_dir)
        self.directory = self.data_dir / SCREENSHOTS_DIRNAME
        self.directory.mkdir(parents=True, exist_ok=True)
        self._backends = list(backends)
        self._clock = clock
        self._lock = threading.Lock()

    @property
    def backend_names(self) -> list[str]:
        return [b.name for b in self._backends]

    def capture(self) -> str:
        """Capture the desktop; return the image path relative to the data directory (POSIX style)."""
        with self._lock:
            when = self._clock()
            tmp = self.directory / f".capture-{uuid.uuid4().hex}.png"
            errors = []
            for backend in list(self._backends):
                try:
                    backend.grab_png(tmp)
                    if not tmp.is_file() or tmp.stat().st_size == 0:
                        raise ScreenshotError("no image was written")
                except Exception as exc:
                    errors.append(f"{backend.name}: {exc}")
                    tmp.unlink(missing_ok=True)
                    continue
                if backend is not self._backends[0]:
                    # Try the backend that worked first next time.
                    self._backends.remove(backend)
                    self._backends.insert(0, backend)
                    log.info("Screenshot backend %s is now preferred", backend.name)
                break
            else:
                raise ScreenshotError("; ".join(errors))
            final = self._place(tmp, when)
            return final.relative_to(self.data_dir).as_posix()

    def _place(self, tmp: Path, when: datetime) -> Path:
        """Give ``tmp`` its final name without ever overwriting an existing file."""
        stem = when.strftime("%Y-%m-%d_%H%M%S") + f"_{when.microsecond // 1000:03d}"
        try:
            for n in range(10_000):
                target = self.directory / (f"{stem}.png" if n == 0 else f"{stem}-{n}.png")
                try:
                    os.link(tmp, target)  # fails atomically if target exists
                except FileExistsError:
                    continue
                except OSError:
                    # Filesystem without hard links: check-then-rename under our lock.
                    if target.exists():
                        continue
                    os.replace(tmp, target)
                    return target
                return target
            raise ScreenshotError(f"no free file name for {stem}.png")
        finally:
            tmp.unlink(missing_ok=True)


def delete_screenshots(data_dir: Path) -> int:
    """Delete the screenshots lazy-anki-language-cards created in ``data_dir``; return how many were removed."""
    directory = Path(data_dir) / SCREENSHOTS_DIRNAME
    removed = 0
    if not directory.is_dir():
        return 0
    for path in directory.iterdir():
        if path.is_file() and (SCREENSHOT_NAME.match(path.name) or path.name.startswith(".capture-")):
            path.unlink(missing_ok=True)
            removed += 1
    try:
        directory.rmdir()  # only succeeds if nothing else is left in it
    except OSError:
        pass
    return removed
