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

export function valuePortfolio(
  portfolio: Portfolio | null,
  prices: Record<string, Quote>,
): LiveValuation | null {
  if (portfolio === null) return null;

  let positionsValue = 0;
  let costBasis = 0;
  const unpricedTickers: string[] = [];

  for (const position of portfolio.positions) {
    const price = markPrice(position, prices);
    if (price === null) {
      unpricedTickers.push(position.ticker);
      continue;
    }
    positionsValue += price * position.quantity;
    costBasis += position.cost_basis;
  }

  return {
    positionsValue,
    totalValue: portfolio.cash_balance + positionsValue,
    costBasis,
    unpricedTickers,
  };
}
