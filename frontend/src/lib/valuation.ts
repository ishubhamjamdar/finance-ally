/**
 * Marking the portfolio to the live stream.
 *
 * `GET /api/portfolio` is fetched once and is correct for the instant it was
 * read. The header has to show a total that moves with the prices arriving
 * twice a second, so positions are re-marked here against the stream rather
 * than by re-fetching the endpoint on every tick.
 *
 * The rule mirrors `backend/app/portfolio.py::_value` exactly, and the part
 * worth stating is what happens to a position with no price: it is **excluded
 * from the total and named**, never counted as zero. A total that quietly
 * omits a holding is indistinguishable from one that lost its value.
 */

import type { Portfolio, Position, Quote } from "./types";

export interface LiveValuation {
  /** Sum of the positions that could be marked. */
  positionsValue: number;
  /** Cash plus `positionsValue`. */
  totalValue: number;
  /**
   * What those same positions cost — **not** `portfolio.cost_basis`.
   *
   * The endpoint's figure covers the positions that had a cached price when it
   * was fetched, which is a different set from the one marked here as soon as
   * the stream prices something that was unpriced a moment ago. Dividing a
   * live numerator by that stale denominator is not a rounding error: holding
   * MSFT at 4,000 priced and AAPL at 2,000 unpriced, the header would read
   * +55% instead of +3% the instant AAPL's first quote arrived.
   */
  costBasis: number;
  /** Held tickers with neither a streamed nor a fetched price. */
  unpricedTickers: string[];
}

/**
 * The live price for a position: the stream first, then the mark the portfolio
 * endpoint returned, then nothing.
 *
 * The fetched `current_price` is the fallback rather than the primary because
 * it ages — it was read from the same cache the stream is now delivering. It
 * is still far better than dropping the position, which is what the third case
 * has to do.
 */
export function markPrice(position: Position, prices: Record<string, Quote>): number | null {
  const streamed = prices[position.ticker]?.price;
  if (Number.isFinite(streamed)) return streamed;
  if (Number.isFinite(position.current_price ?? NaN)) return position.current_price;
  return null;
}

/**
 * One position, marked to the stream.
 *
 * Every derived figure is `null` when the position could not be marked, and
 * the positions table and the heatmap both read them straight out — so there
 * is one implementation of "what is this holding worth right now" rather than
 * one per panel, drifting apart the first time a rounding rule changes.
 */
export interface LivePosition {
  ticker: string;
  quantity: number;
  avgCost: number;
  costBasis: number;
  /** The stream's price, else the fetched mark, else null. */
  price: number | null;
  marketValue: number | null;
  unrealizedPnl: number | null;
  unrealizedPnlPercent: number | null;
  /**
   * Share of the *marked* positions' value, 0–1 — what the heatmap sizes by.
   *
   * Null for an unpriced holding, which therefore gets no tile. Sizing it by
   * cost instead would put a rectangle of one unit beside rectangles of
   * another and call the picture a portfolio.
   */
  weight: number | null;
}

/** Positions marked to the live stream, in the order the endpoint returned them. */
export function markPositions(
  portfolio: Portfolio | null,
  prices: Record<string, Quote>,
): LivePosition[] {
  if (portfolio === null) return [];

  const marked = portfolio.positions.map((position) => {
    const price = markPrice(position, prices);
    const marketValue = price === null ? null : price * position.quantity;
    const unrealizedPnl = marketValue === null ? null : marketValue - position.cost_basis;

    return {
      ticker: position.ticker,
      quantity: position.quantity,
      avgCost: position.avg_cost,
      costBasis: position.cost_basis,
      price,
      marketValue,
      unrealizedPnl,
      unrealizedPnlPercent:
        unrealizedPnl === null || position.cost_basis === 0
          ? null
          : (unrealizedPnl / position.cost_basis) * 100,
      weight: null as number | null,
    };
  });

  const total = marked.reduce((sum, position) => sum + (position.marketValue ?? 0), 0);
  if (total > 0) {
    for (const position of marked) {
      position.weight = position.marketValue === null ? null : position.marketValue / total;
    }
  }

  return marked;
}

export function valuePortfolio(
  portfolio: Portfolio | null,
  prices: Record<string, Quote>,
): LiveValuation | null {
  if (portfolio === null) return null;

  let positionsValue = 0;
  let costBasis = 0;
  const unpricedTickers: string[] = [];

  for (const position of markPositions(portfolio, prices)) {
    if (position.marketValue === null) {
      unpricedTickers.push(position.ticker);
      continue;
    }
    positionsValue += position.marketValue;
    costBasis += position.costBasis;
  }

  return {
    positionsValue,
    totalValue: portfolio.cash_balance + positionsValue,
    costBasis,
    unpricedTickers,
  };
}
