import json
import threading

import pytest

from lazy_anki_language_cards.storage import InvalidAnnotation, RecordNotFound, RecordStore, StorageError


def read_file(store):
    return json.loads(store.path.read_text(encoding="utf-8"))


def test_initializes_missing_file(store):
    assert store.path.exists()
    assert read_file(store) == []


def test_add_writes_exact_schema(store):
    store.add("Hello, wörld! 👋  tabs\tand\nnewlines", "screenshots/a.png")
    assert read_file(store) == [
        {"sentence": "Hello, wörld! 👋  tabs\tand\nnewlines", "image_path": "screenshots/a.png", "words": []}
    ]
    # Human-readable UTF-8, not \u escapes.
    raw = store.path.read_text(encoding="utf-8")
    assert "wörld" in raw and "👋" in raw and "\n  " in raw


def test_add_rejects_empty_text(store):
    with pytest.raises(ValueError):
        store.add("   \n", None)


def test_records_keep_insertion_order(store):
    for text in ("one", "two", "three"):
        store.add(text, None)
    assert [r["sentence"] for r in read_file(store)] == ["one", "two", "three"]


def test_add_word_validates_and_deduplicates(store):
    rec = store.add("The cat sat on the cat mat.", None)
    record, added = store.add_word(rec["id"], "cat")
    assert added and record["words"] == ["cat"]
    record, added = store.add_word(rec["id"], "cat")
    assert not added and record["words"] == ["cat"]
    store.add_word(rec["id"], "The")  # case preserved, distinct from "the"
    store.add_word(rec["id"], "the")
    store.add_word(rec["id"], "mat.")
    assert read_file(store)[0]["words"] == ["cat", "The", "the", "mat."]
    with pytest.raises(InvalidAnnotation):
        store.add_word(rec["id"], "dog")
    with pytest.raises(InvalidAnnotation):
        store.add_word(rec["id"], "  ")
    with pytest.raises(RecordNotFound):
        store.add_word(999, "cat")


def test_remove_word(store):
    rec = store.add("alpha beta", None)
    store.add_word(rec["id"], "alpha")
    store.add_word(rec["id"], "beta")
    assert store.remove_word(rec["id"], "alpha")["words"] == ["beta"]
    with pytest.raises(InvalidAnnotation):
        store.remove_word(rec["id"], "alpha")


def test_delete(store):
    a = store.add("a", None)
    b = store.add("b", None)
    store.delete(a["id"])
    assert [r["sentence"] for r in read_file(store)] == ["b"]
    with pytest.raises(RecordNotFound):
        store.delete(a["id"])
    assert store.get(b["id"])["sentence"] == "b"


def test_undo_removes_latest_even_after_deleting_older(store):
    a = store.add("first", None)
    store.add("second", None)
    c = store.add("third", None)
    store.delete(a["id"])
    assert store.undo_latest()["id"] == c["id"]
    assert [r["sentence"] for r in read_file(store)] == ["second"]
    assert store.undo_latest()["sentence"] == "second"
    assert store.undo_latest() is None


def test_undo_after_deleting_latest_removes_previous(store):
    store.add("first", None)
    second = store.add("second", None)
    store.delete(second["id"])
    assert store.undo_latest()["sentence"] == "first"


def test_persists_across_restart(store):
    rec = store.add("Persist me, please", "screenshots/x.png")
    store.add_word(rec["id"], "Persist")
    reloaded = RecordStore(store.path)
    records = reloaded.list_records()
    assert records[0]["sentence"] == "Persist me, please"
    assert records[0]["image_path"] == "screenshots/x.png"
    assert records[0]["words"] == ["Persist"]


def test_corrupt_file_is_not_overwritten(tmp_path):
    path = tmp_path / "records.json"
    path.write_text("[{not json", encoding="utf-8")
    with pytest.raises(StorageError, match="not valid JSON"):
        RecordStore(path)
    assert path.read_text(encoding="utf-8") == "[{not json"


def test_invalid_schema_is_reported(tmp_path):
    path = tmp_path / "records.json"
    path.write_text(json.dumps([{"sentence": 5}]), encoding="utf-8")
    with pytest.raises(StorageError, match="'sentence' must be a string"):
        RecordStore(path)


def test_failed_write_leaves_memory_and_file_unchanged(store, monkeypatch):
    store.add("kept", None)
    import lazy_anki_language_cards.storage as storage_module

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(storage_module, "atomic_write_json", boom)
    with pytest.raises(StorageError):
        store.add("lost", None)
    assert [r["sentence"] for r in store.list_records()] == ["kept"]
    assert [r["sentence"] for r in read_file(store)] == ["kept"]


def test_no_temp_files_left_behind(store):
    for i in range(5):
        store.add(f"text {i}", None)
    assert sorted(p.name for p in store.path.parent.iterdir()) == ["records.json"]


def test_concurrent_operations_keep_file_valid(store):
    seed = [store.add(f"seed {i}", None) for i in range(20)]
    errors = []

    def adder(n):
        try:
            for i in range(25):
                store.add(f"thread {n} item {i}", None)
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    def annotator():
        try:
            for rec in seed:
                store.add_word(rec["id"], "seed")
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    def deleter():
        try:
            for rec in seed[10:]:
                store.delete(rec["id"])
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=adder, args=(n,)) for n in range(4)]
    threads += [threading.Thread(target=annotator), threading.Thread(target=deleter)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Annotating a record the deleter already removed is a legitimate RecordNotFound.
    assert all(isinstance(e, RecordNotFound) for e in errors)
    on_disk = read_file(store)
    assert len(on_disk) == 10 + 4 * 25
    assert on_disk == [{k: r[k] for k in ("sentence", "image_path", "words")} for r in store.list_records()]
    assert all(r["words"] == ["seed"] for r in on_disk[:10])


def test_listener_called_with_version(store):
    seen = []
    store.add_listener(seen.append)
    store.add("x", None)
    store.undo_latest()
    assert seen == [1, 2]


def test_close_blocks_changes(store):
    rec = store.add("x", None)
    store.close()
    with pytest.raises(StorageError, match="shutting down"):
        store.add("y", None)
    with pytest.raises(StorageError):
        store.add_word(rec["id"], "x")
    assert [r["sentence"] for r in read_file(store)] == ["x"]


def test_delete_file(store):
    store.add("x", None)
    store.delete_file()
    assert not store.path.exists()
    with pytest.raises(StorageError):
        store.add("y", None)
    assert not store.path.exists()


def test_read_file_text_is_exact(store):
    store.add("é", None)
    version, text = store.read_file_text()
    assert version == 1 and text == store.path.read_text(encoding="utf-8")
