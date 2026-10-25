"""In-process publish/subscribe used to push live updates to browsers via SSE."""

from __future__ import annotations

import queue
import threading
from typing import Any

SHUTDOWN_EVENT: dict[str, Any] = {"type": "shutdown"}


class EventBus:
    def __init__(self, max_queue: int = 256):
        self._lock = threading.Lock()
        self._subscribers: set[queue.Queue] = set()
        self._closed = False
        self._max_queue = max_queue

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=self._max_queue)
        with self._lock:
            if self._closed:
                q.put_nowait(SHUTDOWN_EVENT)
            else:
                self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(event)
            except queue.Full:
                # A stalled client already has "changed" events queued; it will
                # refetch everything when it catches up, so dropping is safe.
                pass

    def close(self, event: dict[str, Any] | None = None) -> None:
        """Tell every subscriber the application is stopping (``event`` must have type "shutdown")."""
        event = event or SHUTDOWN_EVENT
        with self._lock:
            self._closed = True
            subscribers = list(self._subscribers)
            self._subscribers.clear()
        for q in subscribers:
            while True:
                try:
                    q.put_nowait(event)
                    break
                except queue.Full:
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        pass
