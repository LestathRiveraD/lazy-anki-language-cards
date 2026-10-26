"""Application startup, lifecycle and shutdown."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import sys
import threading
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .clipboard import BACKENDS as CLIPBOARD_BACKENDS
from .clipboard import ClipboardError, ClipboardUnavailable, create_clipboard
from .events import EventBus
from .monitor import ClipboardMonitor
from .platform_env import NO_SESSION_HELP, detect_session
from .screenshot import (
    BACKEND_NAMES,
    ScreenshotCapturer,
    ScreenshotUnavailable,
    build_screenshot_backends,
    delete_screenshots,
)
from .storage import RecordStore, StorageError
from .web import create_app

log = logging.getLogger("lazy_anki_language_cards")

DEFAULT_PORT = 8765
RECORDS_FILENAME = "records.json"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost")


class StartupError(Exception):
    """Startup failed for a reason the user can fix; the message explains how."""


@dataclass(frozen=True)
class QuitChoice:
    discard: bool = False  # delete records.json
    discard_screenshots: bool = False  # also delete the screenshots (only with discard)


def ask_quit_choice(records_file: Path, count: int, ask=input) -> QuitChoice:
    """Ask in the terminal whether to keep the dataset. Anything unexpected keeps it."""
    noun = "record" if count == 1 else "records"
    try:
        while True:
            answer = ask(f"\nKeep {records_file} ({count} {noun})? [K]eep / [d]iscard: ").strip().lower()
            if answer in ("", "k", "keep"):
                return QuitChoice()
            if answer in ("d", "discard"):
                break
            print("Please answer k (keep) or d (discard).")
        shots = ask("Also delete the screenshots? [y/N]: ").strip().lower()
        return QuitChoice(discard=True, discard_screenshots=shots in ("y", "yes"))
    except (EOFError, KeyboardInterrupt):
        print()
        return QuitChoice()


def default_data_dir() -> Path:
    if os.environ.get("LAZY_ANKI_LANGUAGE_CARDS_DATA_DIR"):
        return Path(os.environ["LAZY_ANKI_LANGUAGE_CARDS_DATA_DIR"]).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / "lazy-anki-language-cards"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lazy-anki-language-cards",
        description="Record copied text with a screenshot and annotate it in your browser.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="where records.json and screenshots/ are stored "
        "(default: $LAZY_ANKI_LANGUAGE_CARDS_DATA_DIR or ~/.local/share/lazy-anki-language-cards)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="interface to listen on (default: 127.0.0.1)")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"port to listen on (default: {DEFAULT_PORT}, or any free port if that one is busy)",
    )
    parser.add_argument("--no-browser", action="store_true", help="do not open the web page automatically")
    parser.add_argument(
        "--interval", type=float, default=0.3, help="clipboard polling interval in seconds (default: 0.3)"
    )
    parser.add_argument(
        "--clipboard-backend", choices=["auto", *CLIPBOARD_BACKENDS], default="auto", help="clipboard tool to use"
    )
    parser.add_argument(
        "--screenshot-backend", choices=["auto", *BACKEND_NAMES], default="auto", help="screenshot tool to use"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="log debug details and HTTP requests")
    parser.add_argument("--version", action="version", version=f"lazy-anki-language-cards {__version__}")
    args = parser.parse_args(argv)
    if args.interval < 0.05:
        parser.error("--interval must be at least 0.05 seconds")
    if args.port is not None and not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    return args


def open_listening_socket(host: str, port: int | None) -> socket.socket:
    """Bind ``host:port``; with no explicit port, fall back to any free port if the default is busy."""
    candidates = [port] if port is not None else [DEFAULT_PORT, 0]
    last_error: OSError | None = None
    for candidate in candidates:
        try:
            infos = socket.getaddrinfo(host, candidate, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise StartupError(f"Cannot resolve host {host!r}: {exc}") from exc
        family, _, _, _, address = infos[0]
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(address)
            sock.listen(128)
            return sock
        except OSError as exc:
            sock.close()
            last_error = exc
            if port is None and candidate == DEFAULT_PORT:
                log.info("Port %d is busy; using a free port instead", DEFAULT_PORT)
    if port is not None:
        raise StartupError(
            f"Cannot listen on {host}:{port}: {last_error.strerror if last_error else 'unknown error'}.\n"
            "Another program (or another lazy-anki-language-cards) may be using it. Pick a different --port, "
            "or omit --port to choose a free one automatically."
        )
    raise StartupError(f"Cannot start the web server on {host}: {last_error}")


class Application:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.data_dir: Path = (args.data_dir or default_data_dir()).expanduser().resolve()
        self.stop_requested = threading.Event()
        self.quit_choice: QuitChoice | None = None  # set when the page's Quit dialog was used
        self.ask_in_terminal = False  # set when Ctrl+C was pressed in the terminal
        self._stopped = False
        self.url = ""

    def start(self) -> None:
        session = detect_session()
        if session.kind == "unknown":
            raise StartupError(NO_SESSION_HELP)
        log.info("Desktop: %s", session.describe())

        try:
            clipboard = create_clipboard(session, self.args.clipboard_backend)
            screenshot_backends = build_screenshot_backends(session, self.args.screenshot_backend)
        except (ClipboardUnavailable, ScreenshotUnavailable) as exc:
            raise StartupError(str(exc)) from exc

        try:
            self.store = RecordStore(self.data_dir / RECORDS_FILENAME)
            capturer = ScreenshotCapturer(self.data_dir, screenshot_backends)
        except StorageError as exc:
            raise StartupError(str(exc)) from exc
        except OSError as exc:
            raise StartupError(f"Cannot prepare data directory {self.data_dir}: {exc}") from exc

        self.bus = EventBus()
        self.store.add_listener(lambda version: self.bus.publish({"type": "changed", "version": version}))
        self.monitor = ClipboardMonitor(
            clipboard,
            capturer,
            self.store,
            interval=self.args.interval,
            on_error=lambda message: self.bus.publish({"type": "app-error", "message": message}),
        )
        try:
            self.monitor.prime()
        except ClipboardError as exc:
            raise StartupError(f"Cannot read the clipboard with {clipboard.name}: {exc}") from exc

        app = create_app(
            self.store,
            self.bus,
            data_dir=self.data_dir,
            capture=self.monitor,
            request_shutdown=self.request_quit,
            info={"clipboard_backend": clipboard.name, "screenshot_backends": capturer.backend_names},
        )
        self.server = self._make_server(app)
        port = self.server.port
        if self.args.host in LOOPBACK_HOSTS:
            app.config["ALLOWED_HOSTS"] = {f"{h}:{port}" for h in LOOPBACK_HOSTS}
        else:
            log.warning("Listening on %s: the page is reachable from other machines on your network.", self.args.host)
        url_host = "127.0.0.1" if self.args.host in ("0.0.0.0", "::") else self.args.host
        self.url = f"http://{url_host}:{port}/"

        self.monitor.start()
        self.server_thread = threading.Thread(target=self.server.serve_forever, name="http-server", daemon=True)
        self.server_thread.start()

        log.info("Clipboard backend: %s | screenshot backends: %s", clipboard.name, ", ".join(capturer.backend_names))
        log.info("Records file:   %s", self.store.path)
        log.info("Screenshots:    %s", capturer.directory)
        log.info("Web interface:  %s", self.url)
        log.info("Shortcuts on the page: A annotates the selection, Ctrl+Z removes the latest record.")
        log.info("Stop with Ctrl+C here or the Quit button; you will be asked whether to keep records.json.")

    def _make_server(self, app):
        from werkzeug.serving import make_server

        sock = open_listening_socket(self.args.host, self.args.port)
        try:
            # Passing our own socket avoids werkzeug's sys.exit() on bind errors.
            return make_server(self.args.host, sock.getsockname()[1], app, threaded=True, fd=sock.fileno())
        finally:
            sock.close()  # werkzeug holds its own duplicate of the descriptor

    def request_quit(self, discard: bool = False, discard_screenshots: bool = False) -> None:
        if not self.stop_requested.is_set():
            self.quit_choice = QuitChoice(discard, discard_screenshots)
            self.stop_requested.set()

    def open_browser(self) -> None:
        def _open() -> None:
            try:
                if not webbrowser.open(self.url, new=2):
                    log.warning("Could not open a browser automatically; visit %s", self.url)
            except Exception as exc:
                log.warning("Could not open a browser (%s); visit %s", exc, self.url)

        threading.Thread(target=_open, name="open-browser", daemon=True).start()

    def wait(self) -> None:
        def on_signal(signum, _frame):
            log.info("Received %s, shutting down (press Ctrl+C again to force)", signal.Signals(signum).name)
            # A second Ctrl+C raises KeyboardInterrupt, which keeps the data and exits.
            signal.signal(signal.SIGINT, signal.default_int_handler)
            self.ask_in_terminal = signum == signal.SIGINT and sys.stdin is not None and sys.stdin.isatty()
            self.stop_requested.set()

        signal.signal(signal.SIGINT, on_signal)
        signal.signal(signal.SIGTERM, on_signal)
        while not self.stop_requested.wait(0.5):
            pass

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        log.info("Stopping clipboard monitor...")
        self.monitor.stop()
        self.monitor.join(timeout=20)
        if self.monitor.is_alive():
            log.warning("Clipboard monitor did not stop in time; exiting anyway")
        self.store.close()  # requests still in flight can no longer change the file

        choice = self.quit_choice
        if choice is None:
            if self.ask_in_terminal:
                choice = ask_quit_choice(self.store.path, len(self.store.list_records()))
            else:
                choice = QuitChoice()

        # Ends open SSE streams and tells pages the app stopped.
        self.bus.close({"type": "shutdown", "discarded": choice.discard})
        self.server.shutdown()
        self.server.server_close()
        self.finish(choice)

    def finish(self, choice: QuitChoice) -> None:
        """Keep or delete the dataset according to ``choice``."""
        if not choice.discard:
            log.info("Stopped. Kept %s", self.store.path)
            return
        try:
            self.store.delete_file()
            log.info("Deleted %s", self.store.path)
        except StorageError as exc:
            log.error("%s", exc)
        if choice.discard_screenshots:
            try:
                removed = delete_screenshots(self.data_dir)
                log.info("Deleted %d screenshot(s)", removed)
            except OSError as exc:
                log.error("Could not delete screenshots: %s", exc)
        else:
            log.info("Screenshots kept in %s", self.data_dir / "screenshots")
        log.info("Stopped.")


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("werkzeug").setLevel(logging.INFO if verbose else logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.verbose)
    application = Application(args)
    try:
        application.start()
    except StartupError as exc:
        print(f"lazy-anki-language-cards: error: {exc}", file=sys.stderr)
        return 1
    try:
        if not args.no_browser:
            application.open_browser()
        application.wait()
    finally:
        application.stop()
    return 0
