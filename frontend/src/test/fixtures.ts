/**
 * Builders for the shapes the backend sends.
 *
 * Every field is populated, in the same form `to_dict()` produces it, so a
 * test cannot pass because it happened to touch only the fields the fixture
 * bothered to define.
 */

import type { Portfolio, Position, Quote, WatchlistRow } from "@/lib/types";

export function makeQuote(ticker: string, price: number, overrides: Partial<Quote> = {}): Quote {
  const previousPrice = overrides.previous_price ?? price;
  const previousClose = overrides.previous_close === undefined ? price : overrides.previous_close;
  const dayChange = previousClose === null ? null : price - previousClose;

  return {
    ticker,
    price,
    previous_price: previousPrice,
    timestamp: 1_760_000_000,
    change: price - previousPrice,
    change_percent: previousPrice === 0 ? 0 : ((price - previousPrice) / previousPrice) * 100,
    direction: price > previousPrice ? "up" : price < previousPrice ? "down" : "flat",
    previous_close: previousClose,
    day_change: dayChange,
    day_change_percent:
      previousClose === null || previousClose === 0 || dayChange === null
        ? null
        : (dayChange / previousClose) * 100,
    ...overrides,
  };
}

/** `{ AAPL: 190, MSFT: 420 }` → a `data:` frame's payload. */
export function makeFrame(prices: Record<string, number>): Record<string, Quote> {
  return Object.fromEntries(
    Object.entries(prices).map(([ticker, price]) => [ticker, makeQuote(ticker, price)]),
  );
}

export function makeWatchlistRow(
  ticker: string,
  quote: Quote | null = null,
  addedAt = "2026-08-12T09:30:00Z",
): WatchlistRow {
  return { ticker, added_at: addedAt, quote };
}

export function makePosition(
  ticker: string,
  quantity: number,
  avgCost: number,
  currentPrice: number | null,
): Position {
  const costBasis = avgCost * quantity;
  const marketValue = currentPrice === null ? null : currentPrice * quantity;

  return {
    ticker,
    quantity,
    avg_cost: avgCost,
    cost_basis: costBasis,
    current_price: currentPrice,
    market_value: marketValue,
    unrealized_pnl: marketValue === null ? null : marketValue - costBasis,
    unrealized_pnl_percent:
      marketValue === null || costBasis === 0 ? null : ((marketValue - costBasis) / costBasis) * 100,
  };
}

export function makePortfolio(cash: number, positions: Position[] = []): Portfolio {
  const priced = positions.filter((position) => position.market_value !== null);
  const positionsValue = priced.reduce((total, position) => total + (position.market_value ?? 0), 0);
  const costBasis = priced.reduce((total, position) => total + position.cost_basis, 0);

  return {
    cash_balance: cash,
    positions,
    positions_value: positionsValue,
    total_value: cash + positionsValue,
    cost_basis: costBasis,
    unrealized_pnl: positionsValue - costBasis,
    unrealized_pnl_percent: costBasis === 0 ? null : ((positionsValue - costBasis) / costBasis) * 100,
    unpriced_tickers: positions
      .filter((position) => position.market_value === null)
      .map((position) => position.ticker),
  };
}
