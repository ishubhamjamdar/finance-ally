"""Bounded, multi-reader log of notable market events."""

from __future__ import annotations

from collections import deque
from threading import Lock

from .models import MarketEvent


class EventLog:
    """Append-only ring buffer of MarketEvents, read by cursor.

    Producers append; each SSE client keeps its own cursor and asks for
    everything since. A drain-style queue would mean the first client to poll
    consumes the event and every other client never sees it. Bounded, so a
    long-running server cannot grow without limit and a client reconnecting
    after an outage simply skips ahead.
    """

    def __init__(self, capacity: int = 200) -> None:
        self._events: deque[tuple[int, MarketEvent]] = deque(maxlen=capacity)
        self._next_id: int = 0
        self._lock = Lock()

    def append(self, event: MarketEvent) -> None:
        with self._lock:
            self._events.append((self._next_id, event))
            self._next_id += 1

    def extend(self, events: list[MarketEvent]) -> None:
        for event in events:
            self.append(event)

    def since(self, cursor: int) -> tuple[int, list[MarketEvent]]:
        """Events with id >= cursor, plus the cursor to pass next time.

        Pass cursor=-1 on connect to start at 'now' and skip the backlog.
        """
        with self._lock:
            if cursor < 0:
                return self._next_id, []
            fresh = [event for event_id, event in self._events if event_id >= cursor]
            return self._next_id, fresh

    @property
    def cursor(self) -> int:
        with self._lock:
            return self._next_id

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)
