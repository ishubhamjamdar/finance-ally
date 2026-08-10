"""Database subsystem for FinAlly.

The rest of the backend imports from here and never from a submodule — the
same contract `app.market` keeps.
"""

from .database import (
    DEFAULT_USER_ID,
    REQUIRED_TABLES,
    STARTING_CASH,
    connect,
    ensure_initialized,
    get_db_path,
    load_tracked_tickers,
    read_transaction,
    transaction,
    utc_now,
)
from .repository import (
    Position,
    Snapshot,
    Trade,
    WatchlistEntry,
    add_watchlist_entry,
    apply_position,
    delete_watchlist_entry,
    get_cash_balance,
    get_position,
    insert_snapshot,
    insert_trade,
    list_positions,
    list_snapshots,
    list_trades,
    list_watchlist,
    set_cash_balance,
)

__all__ = [
    "DEFAULT_USER_ID",
    "REQUIRED_TABLES",
    "STARTING_CASH",
    "Position",
    "Snapshot",
    "Trade",
    "WatchlistEntry",
    "add_watchlist_entry",
    "apply_position",
    "connect",
    "delete_watchlist_entry",
    "ensure_initialized",
    "get_cash_balance",
    "get_db_path",
    "get_position",
    "insert_snapshot",
    "insert_trade",
    "list_positions",
    "list_snapshots",
    "list_trades",
    "list_watchlist",
    "load_tracked_tickers",
    "read_transaction",
    "set_cash_balance",
    "transaction",
    "utc_now",
]
