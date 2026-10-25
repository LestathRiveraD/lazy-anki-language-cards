import json

from clipnotate.web import API_HEADER


def test_index_page(client):
    response = client.get("/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Clipnotate" in body and "Ctrl+Z" in body


def test_list_records(client, store):
    store.add("first", "screenshots/one two.png")
    store.add("second", None)
    data = client.get("/api/records").get_json()
    assert [r["sentence"] for r in data["records"]] == ["first", "second"]
    assert data["records"][0]["image_url"] == "/media/screenshots/one%20two.png"
    assert data["records"][1]["image_url"] is None
    assert isinstance(data["version"], int) and data["session"]


def test_annotate_flow(client, store):
    rec = store.add("Ünïcode, punctuation… and CASE", None)
    url = f"/api/records/{rec['id']}/words"

    response = client.post(url, json={"word": "punctuation…"})
    assert response.status_code == 200
    assert response.get_json()["added"] is True

    response = client.post(url, json={"word": "punctuation…"})
    assert response.get_json()["added"] is False

    assert client.post(url, json={"word": "not there"}).status_code == 400
    assert client.post(url, json={"word": ""}).status_code == 400
    assert client.post(url, json={}).status_code == 400
    assert client.post(url, data="nope", content_type="text/plain").status_code == 400
    assert client.post("/api/records/999/words", json={"word": "x"}).status_code == 404

    on_disk = json.loads(store.path.read_text(encoding="utf-8"))
    assert on_disk[0]["words"] == ["punctuation…"]

    response = client.delete(url, json={"word": "punctuation…"})
    assert response.status_code == 200 and response.get_json()["record"]["words"] == []


def test_delete_record(client, store):
    a = store.add("a", None)
    store.add("b", None)
    assert client.delete(f"/api/records/{a['id']}").status_code == 200
    assert [r["sentence"] for r in client.get("/api/records").get_json()["records"]] == ["b"]
    assert client.delete(f"/api/records/{a['id']}").status_code == 404


def test_undo(client, store):
    assert client.post("/api/undo").get_json() == {"removed": None}
    a = store.add("a", None)
    store.add("b", None)
    c = store.add("c", None)
    client.delete(f"/api/records/{a['id']}")
    assert client.post("/api/undo").get_json()["removed"]["id"] == c["id"]
    assert [r["sentence"] for r in client.get("/api/records").get_json()["records"]] == ["b"]


def test_mutations_require_api_header(app_env, store):
    app = app_env[0]
    rec = store.add("text", None)
    bare = app.test_client()
    assert bare.post("/api/undo").status_code == 403
    assert bare.delete(f"/api/records/{rec['id']}").status_code == 403
    assert bare.post(f"/api/records/{rec['id']}/words", json={"word": "text"}).status_code == 403
    assert bare.get("/api/records").status_code == 200
    assert len(store.list_records()) == 1


def test_host_header_allow_list(app_env, client):
    app = app_env[0]
    app.config["ALLOWED_HOSTS"] = {"127.0.0.1:8765"}
    assert client.get("/api/records", headers={"Host": "127.0.0.1:8765"}).status_code == 200
    assert client.get("/api/records", headers={"Host": "evil.example:8765"}).status_code == 403


def test_capture_pause(client, app_env):
    capture = app_env[2]
    assert client.post("/api/capture", json={"paused": True}).get_json() == {"paused": True}
    assert capture.paused is True
    assert client.get("/api/status").get_json()["paused"] is True
    assert client.post("/api/capture", json={"paused": "yes"}).status_code == 400


def test_shutdown_endpoint(client, app_env):
    shutdowns = app_env[3]
    assert client.post("/api/shutdown").status_code == 202
    assert shutdowns[-1] == {"discard": False, "discard_screenshots": False}
    assert client.post("/api/shutdown", json={"discard": True, "discard_screenshots": True}).status_code == 202
    assert shutdowns[-1] == {"discard": True, "discard_screenshots": True}
    # Screenshots are only deleted together with the JSON file.
    client.post("/api/shutdown", json={"discard": False, "discard_screenshots": True})
    assert shutdowns[-1] == {"discard": False, "discard_screenshots": False}
    assert client.post("/api/shutdown", json={"discard": "yes"}).status_code == 400


def test_raw_json_file_matches_disk(client, store):
    rec = store.add("Ünïcode “text”", None)
    store.add_word(rec["id"], "text")
    response = client.get("/records.json")
    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert response.get_data(as_text=True) == store.path.read_text(encoding="utf-8")
    assert response.headers["X-Dataset-Version"] == str(store.version)


def test_ignore_copy(client, app_env):
    capture = app_env[2]
    assert client.post("/api/ignore-copy", json={"text": "[\n  {}\n]"}).status_code == 200
    assert capture.ignored == ["[\n  {}\n]"]
    assert client.post("/api/ignore-copy", json={"text": 3}).status_code == 400


def test_media_serving(client, data_dir):
    shots = data_dir / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    (shots / "a.png").write_bytes(b"\x89PNG fake")
    assert client.get("/media/screenshots/a.png").data == b"\x89PNG fake"
    assert client.get("/media/records.json").status_code == 404
    assert client.get("/media/screenshots/../records.json").status_code == 404


def test_event_stream_delivers_changes_and_shutdown(app_env, client, store):
    bus = app_env[1]
    response = client.get("/api/events", buffered=False)
    stream = response.response
    assert next(stream).decode().startswith("retry:")
    store_listener = lambda version: bus.publish({"type": "changed", "version": version})  # noqa: E731
    store.add_listener(store_listener)
    store.add("live", None)
    chunk = next(stream).decode()
    assert chunk.startswith("event: changed") and '"version": 1' in chunk
    bus.close()
    assert next(stream).decode().startswith("event: shutdown")
    response.close()


def test_api_header_constant_matches_frontend():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "clipnotate" / "static" / "app.js").read_text()
    assert f'"{API_HEADER}": "1"' in js
