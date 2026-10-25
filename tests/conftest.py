import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clipnotate.events import EventBus  # noqa: E402
from clipnotate.storage import RecordStore  # noqa: E402
from clipnotate.web import API_HEADER, create_app  # noqa: E402


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path / "data"


@pytest.fixture
def store(data_dir):
    return RecordStore(data_dir / "records.json")


class FakeCapture:
    def __init__(self):
        self.paused = False
        self.ignored = []

    def set_paused(self, paused):
        self.paused = paused

    def ignore_text(self, text):
        self.ignored.append(text)


@pytest.fixture
def app_env(store, data_dir):
    bus = EventBus()
    capture = FakeCapture()
    shutdowns = []
    app = create_app(
        store, bus, data_dir=data_dir, capture=capture, request_shutdown=lambda **kw: shutdowns.append(kw)
    )
    app.testing = True
    return app, bus, capture, shutdowns


@pytest.fixture
def client(app_env):
    app = app_env[0]
    c = app.test_client()
    c.environ_base["HTTP_" + API_HEADER.upper().replace("-", "_")] = "1"
    return c
