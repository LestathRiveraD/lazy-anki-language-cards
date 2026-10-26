import socket

import pytest

from lazy_anki_language_cards.app import (
    DEFAULT_PORT,
    Application,
    QuitChoice,
    StartupError,
    ask_quit_choice,
    open_listening_socket,
    parse_args,
)
from lazy_anki_language_cards.platform_env import detect_session


def test_explicit_busy_port_gives_clear_error():
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen()
    port = blocker.getsockname()[1]
    try:
        with pytest.raises(StartupError, match=f"Cannot listen on 127.0.0.1:{port}"):
            open_listening_socket("127.0.0.1", port)
    finally:
        blocker.close()


def test_default_port_falls_back_to_free_port():
    blocker = socket.socket()
    try:
        blocker.bind(("127.0.0.1", DEFAULT_PORT))
        blocker.listen()
    except OSError:
        pass  # already busy for another reason, which is equally good for this test
    try:
        sock = open_listening_socket("127.0.0.1", None)
        assert sock.getsockname()[1] != DEFAULT_PORT
        sock.close()
    finally:
        blocker.close()


def test_parse_args_validation():
    assert parse_args([]).host == "127.0.0.1"
    with pytest.raises(SystemExit):
        parse_args(["--interval", "0"])


@pytest.mark.parametrize(
    "env, kind",
    [
        ({"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}, "x11"),
        ({"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}, "wayland"),
        ({"WAYLAND_DISPLAY": "wayland-0"}, "wayland"),
        ({"XDG_SESSION_TYPE": "tty", "DISPLAY": "localhost:10.0"}, "x11"),
        ({}, "unknown"),
    ],
)
def test_detect_session(env, kind):
    assert detect_session(env).kind == kind


def answers(*values):
    it = iter(values)

    def ask(_prompt):
        value = next(it)
        if isinstance(value, BaseException):
            raise value
        return value

    return ask


@pytest.mark.parametrize(
    "replies, expected",
    [
        (("",), QuitChoice()),
        (("k",), QuitChoice()),
        (("d", ""), QuitChoice(discard=True)),
        (("what", "discard", "y"), QuitChoice(discard=True, discard_screenshots=True)),
        ((EOFError(),), QuitChoice()),
        ((KeyboardInterrupt(),), QuitChoice()),
        (("d", KeyboardInterrupt()), QuitChoice()),
    ],
)
def test_ask_quit_choice(replies, expected, tmp_path):
    assert ask_quit_choice(tmp_path / "records.json", 3, ask=answers(*replies)) == expected


def make_app_with_data(tmp_path):
    from lazy_anki_language_cards.screenshot import ScreenshotCapturer
    from lazy_anki_language_cards.storage import RecordStore

    class Writer:
        name = "w"

        def grab_png(self, path):
            path.write_bytes(b"png")

    application = Application(parse_args(["--data-dir", str(tmp_path / "data")]))
    application.store = RecordStore(application.data_dir / "records.json")
    shot = ScreenshotCapturer(application.data_dir, [Writer()]).capture()
    application.store.add("text", shot)
    return application, application.data_dir / shot


def test_finish_keep(tmp_path):
    application, shot = make_app_with_data(tmp_path)
    application.finish(QuitChoice())
    assert application.store.path.exists() and shot.exists()


def test_finish_discard_json_only(tmp_path):
    application, shot = make_app_with_data(tmp_path)
    application.finish(QuitChoice(discard=True))
    assert not application.store.path.exists() and shot.exists()


def test_finish_discard_everything(tmp_path):
    application, shot = make_app_with_data(tmp_path)
    application.finish(QuitChoice(discard=True, discard_screenshots=True))
    assert not application.store.path.exists() and not shot.exists()
