"""Shared fixtures for market data tests.

Snapshots are built by parsing a documented payload through the SDK's own
deserialiser rather than with MagicMock. A MagicMock fabricates any attribute
on access, so `snap.last_trade.timestamp` "worked" in tests while raising
AttributeError in production — which is how a totally broken Massive
integration passed thirteen tests.
"""

from contextlib import contextmanager
from unittest.mock import patch

from massive.rest.models.snapshot import TickerSnapshot

# Real Polygon error bodies. The SDK raises BadResponse(body) and discards
# resp.status, so this is exactly what the classifier sees — note that neither
# contains its HTTP status code anywhere, which is why matching on "401"/"403"
# could never work.
UNKNOWN_KEY_BODY = (
    '{"status":"ERROR","request_id":"b1c2d3e4f5a6978899aabbccddeeff00","message":"Unknown API Key"}'
)
NOT_AUTHORIZED_BODY = (
    '{"status":"NOT_AUTHORIZED","request_id":"0f1e2d3c4b5a69788",'
    '"message":"You are not entitled to this data. Please upgrade your plan."}'
)
RATE_LIMITED_BODY = (
    '{"status":"ERROR","request_id":"aabbccddeeff00112233445566778899",'
    '"message":"You\'ve exceeded the maximum requests per minute."}'
)


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


@contextmanager
def offline_massive(snapshots=None, error: str | None = None):
    """Run MassiveDataSource with no network access at all.

    Stubbing `_client` and `_fetch_snapshots` on the instance is NOT enough:
    `start()` overwrites `_client` with a real RESTClient and then calls
    `_refresh_market_status()`, which issues a live GET to api.massive.com.
    Seven such calls were costing ~4.5s of a 6.4s suite and made the tests
    fail offline. Patching the class is the only thing that closes it.

    `snapshots` is a list returned from every fetch; `error` is a response body
    to raise instead.
    """
    from app.market.massive_client import MassiveDataSource

    if error is not None:

        def fetch(self):
            raise Exception(error)
    else:

        def fetch(self):
            return list(snapshots) if snapshots is not None else []

    with patch("app.market.massive_client.RESTClient"):
        with patch.object(MassiveDataSource, "_fetch_snapshots", fetch):
            yield


def massive_source(price_cache, tickers=None, poll_interval=60.0, snapshots=None):
    """A MassiveDataSource wired to answer from fixtures, never the network.

    For tests that drive `_poll_once` / `_poll_loop` directly and so never call
    `start()`. Tests that DO call `start()` need `offline_massive` as well.
    """
    from app.market.massive_client import MassiveDataSource

    source = MassiveDataSource(api_key="k", price_cache=price_cache, poll_interval=poll_interval)
    source._tickers = list(tickers) if tickers is not None else ["AAPL"]
    source._client = object()  # only truthiness is checked
    if snapshots is not None:
        source._fetch_snapshots = lambda: list(snapshots)
    return source
