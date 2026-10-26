"""Flask application: the page, the JSON API, live updates (SSE) and screenshot files."""

from __future__ import annotations

import json
import queue
import secrets
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import quote

from flask import Flask, Response, abort, jsonify, render_template, request, send_from_directory

from .events import EventBus
from .screenshot import SCREENSHOTS_DIRNAME
from .storage import InvalidAnnotation, RecordNotFound, RecordStore, StorageError

# State-changing requests must carry this header. Browsers refuse to send a custom
# header cross-origin without a CORS preflight (which this server never approves),
# so other websites cannot drive the API.
API_HEADER = "X-Lazy-Anki-Language-Cards"
UNDO_SHORTCUT = "Ctrl+Z"
ANNOTATE_SHORTCUT = "A"
KEEPALIVE_SECONDS = 15.0


class CaptureControl(Protocol):
    paused: bool

    def set_paused(self, paused: bool) -> None: ...

    def ignore_text(self, text: str) -> None: ...


def create_app(
    store: RecordStore,
    bus: EventBus,
    *,
    data_dir: Path,
    capture: CaptureControl | None = None,
    request_shutdown: Callable[..., None] | None = None,
    info: dict[str, Any] | None = None,
) -> Flask:
    app = Flask(__name__)
    # Host header allow-list (blocks DNS-rebinding); set by the launcher once the port is known.
    app.config["ALLOWED_HOSTS"] = None
    app.json.sort_keys = False
    app.json.ensure_ascii = False

    data_dir = Path(data_dir)
    session_id = secrets.token_hex(8)
    page_info = {
        "records_file": str(store.path),
        "screenshots_dir": str(data_dir / SCREENSHOTS_DIRNAME),
        "shortcut": UNDO_SHORTCUT,
        "annotate_shortcut": ANNOTATE_SHORTCUT,
        **(info or {}),
    }

    def public(record: dict[str, Any]) -> dict[str, Any]:
        image_path = record["image_path"]
        return {**record, "image_url": f"/media/{quote(image_path)}" if image_path else None}

    def error(message: str, status: int) -> Response:
        response = jsonify(error=message)
        response.status_code = status
        return response

    def json_body() -> dict[str, Any]:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            abort(error("Expected a JSON object body.", 400))
        return body

    @app.before_request
    def guard():
        allowed = app.config["ALLOWED_HOSTS"]
        if allowed is not None and request.host not in allowed:
            return error("Unexpected Host header.", 403)
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(API_HEADER) != "1":
            return error(f"Missing {API_HEADER} header.", 403)
        return None

    @app.errorhandler(RecordNotFound)
    def not_found(exc):
        return error(str(exc.args[0]) if exc.args else "Record not found.", 404)

    @app.errorhandler(InvalidAnnotation)
    def bad_annotation(exc):
        return error(str(exc), 400)

    @app.errorhandler(StorageError)
    def storage_failed(exc):
        return error(str(exc), 500)

    @app.get("/")
    def index():
        return render_template("index.html", **page_info)

    @app.get("/api/status")
    def status():
        return jsonify(paused=capture.paused if capture else False, **page_info)

    @app.get("/records.json")
    def dataset_file():
        # The JSON file exactly as stored on disk, for viewing and copying.
        version, text = store.read_file_text()
        response = Response(text, mimetype="application/json")
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Dataset-Version"] = str(version)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/api/records")
    def list_records():
        version, records = store.snapshot()
        return jsonify(session=session_id, version=version, records=[public(r) for r in records])

    @app.delete("/api/records/<int:record_id>")
    def delete_record(record_id: int):
        return jsonify(removed=public(store.delete(record_id)))

    @app.post("/api/records/<int:record_id>/words")
    def add_word(record_id: int):
        word = json_body().get("word")
        if not isinstance(word, str):
            return error("'word' must be a string.", 400)
        record, added = store.add_word(record_id, word)
        return jsonify(record=public(record), added=added)

    @app.delete("/api/records/<int:record_id>/words")
    def remove_word(record_id: int):
        word = json_body().get("word")
        if not isinstance(word, str):
            return error("'word' must be a string.", 400)
        return jsonify(record=public(store.remove_word(record_id, word)))

    @app.post("/api/undo")
    def undo():
        removed = store.undo_latest()
        return jsonify(removed=public(removed) if removed else None)

    @app.post("/api/capture")
    def set_capture():
        if capture is None:
            return error("Capture control is not available.", 503)
        paused = json_body().get("paused")
        if not isinstance(paused, bool):
            return error("'paused' must be true or false.", 400)
        capture.set_paused(paused)
        bus.publish({"type": "capture", "paused": paused})
        return jsonify(paused=capture.paused)

    @app.post("/api/ignore-copy")
    def ignore_copy():
        if capture is None:
            return error("Capture control is not available.", 503)
        text = json_body().get("text")
        if not isinstance(text, str):
            return error("'text' must be a string.", 400)
        capture.ignore_text(text)
        return jsonify(ignored=True)

    @app.post("/api/shutdown")
    def shutdown():
        if request_shutdown is None:
            return error("Shutdown is not available.", 503)
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return error("Expected a JSON object body.", 400)
        discard = body.get("discard", False)
        discard_screenshots = body.get("discard_screenshots", False)
        if not isinstance(discard, bool) or not isinstance(discard_screenshots, bool):
            return error("'discard' and 'discard_screenshots' must be true or false.", 400)
        request_shutdown(discard=discard, discard_screenshots=discard and discard_screenshots)
        return jsonify(stopping=True, discard=discard), 202

    @app.get("/api/events")
    def events():
        q = bus.subscribe()

        def stream():
            try:
                yield "retry: 2000\n\n"
                while True:
                    try:
                        event = q.get(timeout=KEEPALIVE_SECONDS)
                    except queue.Empty:
                        yield ": keepalive\n\n"
                        continue
                    yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if event["type"] == "shutdown":
                        return
            finally:
                bus.unsubscribe(q)

        return Response(
            stream(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get(f"/media/{SCREENSHOTS_DIRNAME}/<path:filename>")
    def media(filename: str):
        # Only screenshots are served; send_from_directory rejects paths escaping the directory.
        return send_from_directory(data_dir / SCREENSHOTS_DIRNAME, filename, max_age=3600)

    return app
