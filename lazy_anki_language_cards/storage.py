"""Persistent JSON record storage.

The dataset is a JSON array of records in insertion order (oldest first)::

    [{"sentence": "...", "image_path": "screenshots/....png", "words": ["..."]}]

Records get an integer ``id`` in memory so the web interface can address them,
but ids are never written to disk: the file keeps exactly the documented schema.
Every change is written with an atomic replace, so an interrupted write leaves
the previous version of the file intact.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)


class StorageError(Exception):
    """The dataset could not be read or written."""


class RecordNotFound(LookupError):
    """No record with the requested id exists (it may have been deleted)."""


class InvalidAnnotation(ValueError):
    """The submitted word is not a usable substring of the record's sentence."""


@dataclass(frozen=True)
class _Record:
    id: int
    sentence: str
    image_path: str | None
    words: tuple[str, ...]
    # Unknown keys found in a hand-edited file, kept so saving never drops data.
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "sentence": self.sentence,
            "image_path": self.image_path,
            "words": list(self.words),
        }
        data.update(self.extra)
        return data

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sentence": self.sentence,
            "image_path": self.image_path,
            "words": list(self.words),
        }


def atomic_write_json(path: Path, data: Any) -> None:
    """Write ``data`` as indented UTF-8 JSON, replacing ``path`` atomically."""
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
    _fsync_directory(path.parent)


def _fsync_directory(directory: Path) -> None:
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


class RecordStore:
    """Thread-safe owner of the record list and its JSON file."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._records: list[_Record] = []
        self._next_id = 1
        self._version = 0
        self._listeners: list[Callable[[int], None]] = []
        self._closed = False
        self._load()

    # ----------------------------------------------------------------- loading

    def _load(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(f"Cannot create data directory {self.path.parent}: {exc}") from exc

        if not self.path.exists():
            self._write([])
            log.info("Created new dataset %s", self.path)
            return

        try:
            text = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise StorageError(f"Cannot read {self.path}: {exc}") from exc

        if not text.strip():
            self._write([])
            return

        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise StorageError(
                f"{self.path} is not valid JSON ({exc}). The file was left untouched; "
                "fix it by hand or move it away to start a new dataset."
            ) from exc

        self._records = [self._parse_record(item, index) for index, item in enumerate(_as_list(raw, self.path))]

    def _parse_record(self, item: Any, index: int) -> _Record:
        where = f"{self.path}, record #{index}"
        if not isinstance(item, dict):
            raise StorageError(f"{where}: expected an object, got {type(item).__name__}")
        sentence = item.get("sentence")
        image_path = item.get("image_path")
        words = item.get("words", [])
        if not isinstance(sentence, str):
            raise StorageError(f"{where}: 'sentence' must be a string")
        if image_path is not None and not isinstance(image_path, str):
            raise StorageError(f"{where}: 'image_path' must be a string or null")
        if not isinstance(words, list) or not all(isinstance(w, str) for w in words):
            raise StorageError(f"{where}: 'words' must be a list of strings")
        extra = {k: v for k, v in item.items() if k not in ("sentence", "image_path", "words")}
        return _Record(self._take_id(), sentence, image_path, tuple(dict.fromkeys(words)), extra)

    def _take_id(self) -> int:
        record_id = self._next_id
        self._next_id += 1
        return record_id

    # ----------------------------------------------------------------- queries

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    def add_listener(self, callback: Callable[[int], None]) -> None:
        """Call ``callback(version)`` after every successful change."""
        self._listeners.append(callback)

    def snapshot(self) -> tuple[int, list[dict[str, Any]]]:
        """Return ``(version, records)`` with records in insertion order."""
        with self._lock:
            return self._version, [r.to_public() for r in self._records]

    def list_records(self) -> list[dict[str, Any]]:
        return self.snapshot()[1]

    def get(self, record_id: int) -> dict[str, Any]:
        with self._lock:
            return self._find(record_id)[1].to_public()

    def read_file_text(self) -> tuple[int, str]:
        """Return ``(version, text)`` of the JSON file exactly as it is on disk."""
        with self._lock:
            try:
                return self._version, self.path.read_text(encoding="utf-8")
            except OSError as exc:
                raise StorageError(f"Cannot read {self.path}: {exc}") from exc

    # --------------------------------------------------------------- lifecycle

    def close(self) -> None:
        """Refuse further changes; used while the application shuts down."""
        with self._lock:
            self._closed = True

    def delete_file(self) -> None:
        """Close the store and remove the JSON file from disk."""
        with self._lock:
            self._closed = True
            try:
                self.path.unlink(missing_ok=True)
            except OSError as exc:
                raise StorageError(f"Cannot delete {self.path}: {exc}") from exc

    # --------------------------------------------------------------- mutations

    def add(self, sentence: str, image_path: str | None) -> dict[str, Any]:
        if not isinstance(sentence, str) or not sentence.strip():
            raise ValueError("sentence must be a non-empty string")
        with self._lock:
            record = _Record(self._take_id(), sentence, image_path, ())
            self._commit([*self._records, record])
        self._notify()
        return record.to_public()

    def delete(self, record_id: int) -> dict[str, Any]:
        with self._lock:
            index, record = self._find(record_id)
            self._commit(self._records[:index] + self._records[index + 1 :])
        self._notify()
        return record.to_public()

    def undo_latest(self) -> dict[str, Any] | None:
        """Remove the most recently added record that still exists.

        Records are kept in insertion order, so this is always the last element,
        no matter which older records were deleted in between.
        """
        with self._lock:
            if not self._records:
                return None
            record = self._records[-1]
            self._commit(self._records[:-1])
        self._notify()
        return record.to_public()

    def add_word(self, record_id: int, word: str) -> tuple[dict[str, Any], bool]:
        """Add ``word`` to a record. Returns ``(record, added)``; ``added`` is False for duplicates."""
        if not isinstance(word, str) or not word.strip():
            raise InvalidAnnotation("The selection is empty.")
        with self._lock:
            index, record = self._find(record_id)
            if word not in record.sentence:
                raise InvalidAnnotation("The selected text is not part of this record's sentence.")
            if word in record.words:
                return record.to_public(), False
            updated = replace(record, words=(*record.words, word))
            self._commit(self._replaced(index, updated))
        self._notify()
        return updated.to_public(), True

    def remove_word(self, record_id: int, word: str) -> dict[str, Any]:
        with self._lock:
            index, record = self._find(record_id)
            if word not in record.words:
                raise InvalidAnnotation("That annotation does not exist on this record.")
            updated = replace(record, words=tuple(w for w in record.words if w != word))
            self._commit(self._replaced(index, updated))
        self._notify()
        return updated.to_public()

    # ---------------------------------------------------------------- internals

    def _find(self, record_id: int) -> tuple[int, _Record]:
        for index, record in enumerate(self._records):
            if record.id == record_id:
                return index, record
        raise RecordNotFound(f"Record {record_id} does not exist.")

    def _replaced(self, index: int, record: _Record) -> list[_Record]:
        records = list(self._records)
        records[index] = record
        return records

    def _commit(self, records: list[_Record]) -> None:
        if self._closed:
            raise StorageError("The dataset is closed because lazy-anki-language-cards is shutting down.")
        # Write first: memory only changes if the file was saved successfully.
        self._write(records)
        self._records = records
        self._version += 1

    def _write(self, records: list[_Record]) -> None:
        try:
            atomic_write_json(self.path, [r.to_json() for r in records])
        except OSError as exc:
            raise StorageError(f"Cannot write {self.path}: {exc}") from exc

    def _notify(self) -> None:
        version = self.version
        for callback in list(self._listeners):
            try:
                callback(version)
            except Exception:  # a broken listener must never undo a saved change
                log.exception("Record listener failed")


def _as_list(raw: Any, path: Path) -> list[Any]:
    if not isinstance(raw, list):
        raise StorageError(f"{path}: expected a JSON array of records at the top level")
    return raw
