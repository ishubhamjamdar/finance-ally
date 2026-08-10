"""Market data subsystem for FinAlly.

The rest of the backend imports from here and never from a submodule. This
module is the supported contract; everything else is free to move.
"""

from .cache import PriceCache
from .events import EventLog
from .factory import create_market_data_source, create_simulator_source, start_market_data
from .interface import MarketDataSource, PermanentMarketDataError
from .models import TICKER_PATTERN, MarketEvent, PriceUpdate, normalize_ticker
from .seed_prices import DEFAULT_TICKERS
from .stream import create_stream_router

__all__ = [
    "DEFAULT_TICKERS",
    "TICKER_PATTERN",
    "PriceUpdate",
    "MarketEvent",
    "normalize_ticker",
    "PriceCache",
    "EventLog",
    "MarketDataSource",
    "PermanentMarketDataError",
    "create_market_data_source",
    "create_simulator_source",
    "start_market_data",
    "create_stream_router",
]
