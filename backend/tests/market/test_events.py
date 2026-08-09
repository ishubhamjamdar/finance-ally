"""Tests for EventLog."""

from app.market.events import EventLog
from app.market.models import MarketEvent


def _event(ticker: str = "TSLA", magnitude: float = -3.4) -> MarketEvent:
    return MarketEvent(ticker=ticker, magnitude_percent=magnitude, price=241.5)


class TestEventLog:
    def test_append_and_read_from_zero(self):
        log = EventLog()
        log.append(_event())
        cursor, events = log.since(0)
        assert cursor == 1
        assert [e.ticker for e in events] == ["TSLA"]

    def test_negative_cursor_skips_backlog(self):
        """A connecting client passes -1 to start at 'now'."""
        log = EventLog()
        log.append(_event())
        cursor, events = log.since(-1)
        assert events == []
        assert cursor == 1

    def test_cursor_advances_and_does_not_repeat(self):
        log = EventLog()
        log.append(_event("AAPL"))
        cursor, first = log.since(0)
        log.append(_event("NVDA"))
        cursor, second = log.since(cursor)
        assert [e.ticker for e in first] == ["AAPL"]
        assert [e.ticker for e in second] == ["NVDA"]

    def test_every_reader_sees_every_event(self):
        """The reason this is a ring buffer and not a drain queue: two clients
        with independent cursors must both receive the same event."""
        log = EventLog()
        log.append(_event("TSLA"))

        cursor_a, events_a = log.since(0)
        cursor_b, events_b = log.since(0)

        assert [e.ticker for e in events_a] == ["TSLA"]
        assert [e.ticker for e in events_b] == ["TSLA"]

    def test_extend_appends_all(self):
        log = EventLog()
        log.extend([_event("AAPL"), _event("NVDA")])
        _, events = log.since(0)
        assert len(events) == 2

    def test_extend_empty_is_noop(self):
        log = EventLog()
        log.extend([])
        assert log.cursor == 0

    def test_bounded_capacity_drops_oldest(self):
        log = EventLog(capacity=3)
        for i in range(10):
            log.append(_event(f"T{i}"))
        _, events = log.since(0)
        assert len(events) == 3
        assert [e.ticker for e in events] == ["T7", "T8", "T9"]

    def test_cursor_property_reflects_next_id(self):
        log = EventLog()
        assert log.cursor == 0
        log.append(_event())
        assert log.cursor == 1

    def test_stale_cursor_after_eviction_returns_what_remains(self):
        """A client that reconnects after an outage skips ahead rather than failing."""
        log = EventLog(capacity=2)
        for i in range(5):
            log.append(_event(f"T{i}"))
        cursor, events = log.since(0)
        assert cursor == 5
        assert [e.ticker for e in events] == ["T3", "T4"]
