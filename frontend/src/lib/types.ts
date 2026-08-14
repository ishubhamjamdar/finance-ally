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

// --- chat (PLAN.md §8, §9) -------------------------------------------------

export type ChatRole = "user" | "assistant";

/**
 * One action the assistant attempted, and what came of it.
 *
 * **Both outcomes are reported, and both must be rendered.** The model writes
 * its message *before* knowing whether the trade cleared, so a reply can say
 * "Buying 100000 AAPL" beside an action that was refused for insufficient
 * cash. `app/chat.py` says the same thing from the other side. Showing the
 * message without the actions is how a transcript comes to claim a fill that
 * never happened.
 *
 * `summary` is what was attempted ("buy 10 AAPL"); `detail` is the outcome, or
 * the reason there wasn't one. Both are composed by the backend and rendered
 * verbatim — deriving our own wording would be a second implementation of what
 * the trade actually did.
 */
export interface ChatAction {
  kind: "trade" | "watchlist";
  ok: boolean;
  summary: string;
  detail: string;
  /** Null only for an item that failed the schema before either could be read. */
  ticker: string | null;
  /** buy | sell | add | remove. */
  action: string | null;
  /** The fill for a trade, the entry for a watchlist change, null for a refusal. */
  result: Record<string, unknown> | null;
}

/** A stored turn — `GET /api/chat/history`. `actions` is null for a user message. */
export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  actions: ChatAction[] | null;
  created_at: string;
}

export interface ChatHistoryResponse {
  /** Oldest first, as the endpoint documents. */
  messages: ChatMessage[];
}

/** `POST /api/chat` — what was said, what was done, and where that left the account. */
export interface ChatReply {
  message: string;
  actions: ChatAction[];
  /** Read *after* the actions, so a reply that bought something carries the resulting balance. */
  portfolio: Portfolio;
}
