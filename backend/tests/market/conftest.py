"""Shared fixtures for market data tests.

Snapshots are built by parsing a documented payload through the SDK's own
deserialiser rather than with MagicMock. A MagicMock fabricates any attribute
on access, so `snap.last_trade.timestamp` "worked" in tests while raising
AttributeError in production — which is how a totally broken Massive
integration passed thirteen tests.
"""

from massive.rest.models.snapshot import TickerSnapshot


def make_snapshot(
    ticker: str = "AAPL",
    price: float = 190.50,
    ts_ns: int = 1675190399000000000,
    with_trade: bool = True,
    with_min: bool = True,
    with_day: bool = True,
    with_prev_day: bool = True,
) -> TickerSnapshot:
    """Build a TickerSnapshot with exactly the attributes the real API produces.

    Toggle the sub-objects to simulate the plan tiers: Developer+ returns
    `lastTrade`, Starter returns aggregates only, and pre-open returns neither.
    """
    raw: dict = {
        "ticker": ticker,
        "todaysChange": -4.54,
        "todaysChangePerc": -3.50,
        "updated": ts_ns,
    }
    if with_prev_day:
        raw["prevDay"] = {"o": 128.0, "h": 130.0, "l": 127.0, "c": 129.61, "v": 98_000_000}
    if with_day:
        raw["day"] = {"o": 129.61, "h": 130.15, "l": 125.07, "c": price, "v": 111_237_700}
    if with_min:
        raw["min"] = {
            "av": 111_237_700,
            "o": 125.1,
            "h": 125.2,
            "l": 125.0,
            "c": price,
            "t": 1675190340000,
        }
    if with_trade:
        raw["lastTrade"] = {"p": price, "s": 100, "x": 4, "t": ts_ns, "c": [1]}
    return TickerSnapshot.from_dict(raw)
