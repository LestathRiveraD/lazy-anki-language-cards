import json
import subprocess
from datetime import datetime

import pytest

from lazy_anki_language_cards.clipboard import ClipboardError, XclipClipboard
from lazy_anki_language_cards.monitor import ClipboardMonitor
from lazy_anki_language_cards.screenshot import ScreenshotCapturer, ScreenshotError


class FakeClipboard:
    name = "fake"

    def __init__(self, value=None):
        self.value = value

    def read_text(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class FakeCapturer:
    def __init__(self, store, fail=False):
        self.store = store
        self.fail = fail
        self.calls = 0
        self.records_at_capture = []

    def capture(self):
        self.calls += 1
        # Proves the screenshot is taken before the record is saved.
        self.records_at_capture.append(len(self.store.list_records()))
        if self.fail:
            raise ScreenshotError("no display")
        return f"screenshots/shot-{self.calls}.png"


@pytest.fixture
def setup(store):
    clipboard = FakeClipboard("already there")
    capturer = FakeCapturer(store)
    errors = []
    monitor = ClipboardMonitor(clipboard, capturer, store, on_error=errors.append)
    monitor.prime()
    return monitor, clipboard, capturer, errors


def test_existing_clipboard_is_not_recorded(setup, store):
    monitor, *_ = setup
    assert monitor.poll_once() is None
    assert store.list_records() == []


def test_new_text_creates_exactly_one_record(setup, store):
    monitor, clipboard, capturer, _ = setup
    clipboard.value = "  Exact text, with spaces\tand ünïcode ✓\n"
    record = monitor.poll_once()
    for _ in range(5):
        assert monitor.poll_once() is None  # unchanged clipboard
    assert len(store.list_records()) == 1
    assert record["sentence"] == "  Exact text, with spaces\tand ünïcode ✓\n"
    assert record["image_path"] == "screenshots/shot-1.png"
    assert capturer.calls == 1
    assert capturer.records_at_capture == [0]
    on_disk = json.loads(store.path.read_text(encoding="utf-8"))
    assert set(on_disk[0]) == {"sentence", "image_path", "words"}


def test_empty_and_non_text_are_ignored(setup, store):
    monitor, clipboard, capturer, _ = setup
    for value in ("", "   \n\t", None):
        clipboard.value = value
        monitor.poll_once()
    assert store.list_records() == [] and capturer.calls == 0


def test_same_text_copied_again_after_other_content(setup, store):
    monitor, clipboard, *_ = setup
    clipboard.value = "A"
    monitor.poll_once()
    clipboard.value = None  # e.g. an image was copied
    monitor.poll_once()
    clipboard.value = "A"
    monitor.poll_once()
    assert [r["sentence"] for r in store.list_records()] == ["A", "A"]


def test_read_errors_do_not_cause_duplicates(setup, store):
    monitor, clipboard, _, errors = setup
    clipboard.value = "A"
    monitor.poll_once()
    clipboard.value = ClipboardError("owner timed out")
    monitor.poll_once()
    monitor.poll_once()
    clipboard.value = "A"
    monitor.poll_once()
    assert len(store.list_records()) == 1
    assert errors == ["Clipboard read failed: owner timed out"]  # reported once


def test_screenshot_failure_still_saves_record(store):
    clipboard = FakeClipboard(None)
    errors = []
    monitor = ClipboardMonitor(clipboard, FakeCapturer(store, fail=True), store, on_error=errors.append)
    monitor.prime()
    clipboard.value = "text"
    record = monitor.poll_once()
    assert record["image_path"] is None
    assert errors and "Screenshot failed" in errors[0]


def test_pause_ignores_and_does_not_replay(setup, store):
    monitor, clipboard, *_ = setup
    monitor.set_paused(True)
    clipboard.value = "secret"
    monitor.poll_once()
    monitor.set_paused(False)
    monitor.poll_once()
    assert store.list_records() == []
    clipboard.value = "public"
    monitor.poll_once()
    assert [r["sentence"] for r in store.list_records()] == ["public"]


def test_thread_starts_and_stops(setup):
    monitor = setup[0]
    monitor.start()
    monitor.stop()
    monitor.join(timeout=5)
    assert not monitor.is_alive()


# ---------------------------------------------------------------- clipboard backend


def fake_runner(responses):
    def run(argv, **_kwargs):
        key = argv[-1]
        code, out, err = responses[key]
        return subprocess.CompletedProcess(argv, code, out, err)

    return run


def test_xclip_prefers_utf8_text():
    run = fake_runner(
        {
            "TARGETS": (0, b"TARGETS\nimage/png\nSTRING\nUTF8_STRING\n", b""),
            "UTF8_STRING": (0, "naïve “quotes”\n".encode(), b""),
        }
    )
    assert XclipClipboard(runner=run).read_text() == "naïve “quotes”\n"


def test_xclip_non_text_and_empty():
    image_only = fake_runner({"TARGETS": (0, b"TARGETS\nimage/png\n", b"")})
    assert XclipClipboard(runner=image_only).read_text() is None
    empty = fake_runner({"TARGETS": (1, b"", b"Error: target TARGETS not available\n")})
    assert XclipClipboard(runner=empty).read_text() is None


def test_xclip_skips_password_manager_secrets():
    run = fake_runner({"TARGETS": (0, b"UTF8_STRING\nx-kde-passwordManagerHint\n", b"")})
    assert XclipClipboard(runner=run).read_text() is None


def test_xclip_real_errors_raise():
    run = fake_runner({"TARGETS": (1, b"", b"Error: Can't open display: :99\n")})
    with pytest.raises(ClipboardError, match="Can't open display"):
        XclipClipboard(runner=run).read_text()


# ---------------------------------------------------------------- screenshot capturer


class WritingBackend:
    name = "writer"

    def grab_png(self, path):
        path.write_bytes(b"\x89PNG data")


class FailingBackend:
    name = "broken"

    def grab_png(self, path):
        raise RuntimeError("nope")


def test_screenshot_names_are_unique_and_relative(data_dir):
    when = datetime(2026, 10, 24, 0, 53, 0, 1000)
    capturer = ScreenshotCapturer(data_dir, [WritingBackend()], clock=lambda: when)
    paths = [capturer.capture() for _ in range(3)]
    assert paths == [
        "screenshots/2026-10-24_005300_001.png",
        "screenshots/2026-10-24_005300_001-1.png",
        "screenshots/2026-10-24_005300_001-2.png",
    ]
    assert all((data_dir / p).read_bytes() == b"\x89PNG data" for p in paths)
    assert not [p for p in (data_dir / "screenshots").iterdir() if p.name.startswith(".")]


def test_screenshot_falls_back_and_reports(data_dir):
    capturer = ScreenshotCapturer(data_dir, [FailingBackend(), WritingBackend()])
    assert capturer.capture().startswith("screenshots/")
    assert capturer.backend_names == ["writer", "broken"]  # the working one is tried first next time

    only_broken = ScreenshotCapturer(data_dir, [FailingBackend()])
    with pytest.raises(ScreenshotError, match="broken: nope"):
        only_broken.capture()


def test_text_copied_from_page_is_ignored(setup, store):
    monitor, clipboard, capturer, _ = setup
    payload = '[\n  {"sentence": "x"}\n]\n'
    monitor.ignore_text(payload)
    clipboard.value = payload
    assert monitor.poll_once() is None
    assert store.list_records() == [] and capturer.calls == 0
    # The ignore applies once: copying it again later from elsewhere is recorded.
    clipboard.value = "something else"
    monitor.poll_once()
    clipboard.value = payload
    monitor.poll_once()
    assert [r["sentence"] for r in store.list_records()] == ["something else", payload]


def test_ignore_expires(setup, store, monkeypatch):
    import lazy_anki_language_cards.monitor as monitor_module

    monitor, clipboard, *_ = setup
    now = [1000.0]
    monkeypatch.setattr(monitor_module.time, "monotonic", lambda: now[0])
    monitor.ignore_text("late")
    now[0] += monitor_module.IGNORE_SECONDS + 1
    clipboard.value = "late"
    assert monitor.poll_once() is not None


def test_delete_screenshots_only_removes_own_files(data_dir):
    from lazy_anki_language_cards.screenshot import delete_screenshots

    capturer = ScreenshotCapturer(data_dir, [WritingBackend()])
    capturer.capture()
    capturer.capture()
    (capturer.directory / "my-notes.txt").write_text("keep me")
    assert delete_screenshots(data_dir) == 2
    assert [p.name for p in capturer.directory.iterdir()] == ["my-notes.txt"]
    (capturer.directory / "my-notes.txt").unlink()
    capturer.capture()
    assert delete_screenshots(data_dir) == 1
    assert not capturer.directory.exists()  # removed once empty
