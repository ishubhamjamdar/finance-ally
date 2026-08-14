/**
 * The shapes the backend actually returns — PLAN.md §8.
 *
 * Every field here is produced by a `to_dict()` in `backend/app/`: quotes by
 * `PriceUpdate.to_dict`, positions and totals by `PortfolioView.to_dict`,
 * watchlist rows by `app/api/watchlist.py::_row`. When one of those changes,
 * this file is the other half of the contract.
 *
 * `null` is load-bearing throughout and never to be rendered as zero. A quote
 * without a `previous_close` does not know its daily change; a position whose
 * ticker has no cached price does not know its value. The backend is careful
 * to say "unknown" rather than "nothing", and the UI must be too.
 */

export type Direction = "up" | "down" | "flat";

/** One ticker's latest price. The SSE frame is a map of these, keyed by ticker. */
export interface Quote {
  ticker: string;
  price: number;
  /** The previous *tick*, not the previous close. Drives the flash. */
  previous_price: number;
  /** Epoch **seconds**, not milliseconds — it comes from Python's `time.time()`. */
  timestamp: number;
  change: number;
  change_percent: number;
  direction: Direction;
  previous_close: number | null;
  /** Since the previous close. Null when `previous_close` is unknown. */
  day_change: number | null;
  day_change_percent: number | null;
}

/** A `data:` frame from `/api/stream/prices`: every ticker the server knows. */
export type PriceFrame = Record<string, Quote>;

/** An `event: shock` frame — a notable move worth surfacing. */
export interface MarketShock {
  ticker: string;
  /** Signed: -3.4 means down 3.4%. */
  magnitude_percent: number;
  price: number;
  timestamp: number;
}

/** A row of `GET /api/watchlist`. `quote` is null until the source prices it. */
export interface WatchlistRow {
  ticker: string;
  added_at: string;
  quote: Quote | null;
}

export interface WatchlistResponse {
  tickers: WatchlistRow[];
}

export interface Position {
  ticker: string;
  quantity: number;
  avg_cost: number;
  cost_basis: number;
  current_price: number | null;
  market_value: number | null;
  unrealized_pnl: number | null;
  unrealized_pnl_percent: number | null;
}

export interface Portfolio {
  cash_balance: number;
  positions: Position[];
  positions_value: number;
  total_value: number;
  cost_basis: number;
  unrealized_pnl: number;
  unrealized_pnl_percent: number | null;
  /** Held tickers with no cached price. Excluded from the totals, not zeroed. */
  unpriced_tickers: string[];
}

/** One row of `GET /api/portfolio/history` — a `portfolio_snapshots` row. */
export interface Snapshot {
  total_value: number;
  /** ISO timestamp, as the database stored it. */
  recorded_at: string;
}

export interface HistoryResponse {
  /** Oldest first, as the endpoint documents. */
  snapshots: Snapshot[];
}

export type TradeSide = "buy" | "sell";

/** The body of `POST /api/portfolio/trade`. There is no `price` field: the
 *  fill price is read from the server's cache, and naming one is a 422. */
export interface TradeOrder {
  ticker: string;
  side: TradeSide;
  quantity: number;
}

/** The fill the backend recorded. */
export interface TradeFill {
  id: string;
  ticker: string;
  side: TradeSide;
  quantity: number;
  price: number;
  value: number;
  executed_at: string;
}

/** `POST /api/portfolio/trade` returns the fill and the account it left behind. */
export interface TradeResponse {
  trade: TradeFill;
  portfolio: Portfolio;
}

/** `DELETE /api/watchlist/{ticker}`. `still_tracked` when a position keeps it streaming. */
export interface WatchlistRemoval {
  ticker: string;
  removed: boolean;
  still_tracked: boolean;
}
