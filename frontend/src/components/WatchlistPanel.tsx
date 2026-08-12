"use client";

/**
 * The watchlist grid — PLAN.md §10: symbol, live price flashing on change,
 * daily change %, and a sparkline accumulated from the stream.
 *
 * Row order is the order `GET /api/watchlist` returned, which is add order.
 * Sorting by price or change would reshuffle the grid twice a second and make
 * it unreadable — the backend endpoint documents the same reasoning.
 */

import { Sparkline } from "@/components/Sparkline";
import { flashClass, usePriceFlash } from "@/hooks/usePriceFlash";
import { EM_DASH, formatMoney, formatPercent, toneClass } from "@/lib/format";
import type { Quote, WatchlistRow } from "@/lib/types";

interface WatchlistPanelProps {
  rows: WatchlistRow[];
  prices: Record<string, Quote>;
  sparklines: Record<string, number[]>;
  loading?: boolean;
  error?: string | null;
}

export function WatchlistPanel({
  rows,
  prices,
  sparklines,
  loading = false,
  error = null,
}: WatchlistPanelProps) {
  return (
    <section
      className="flex min-h-0 flex-col rounded border border-edge bg-panel"
      aria-labelledby="watchlist-heading"
    >
      <header className="flex items-baseline justify-between border-b border-edge px-3 py-2">
        <h2
          id="watchlist-heading"
          className="text-[11px] font-semibold tracking-[0.14em] text-muted uppercase"
        >
          Watchlist
        </h2>
        <span className="font-mono text-[11px] text-faint">{rows.length}</span>
      </header>

      {error !== null ? (
        <p className="px-3 py-6 text-center text-xs text-down">{error}</p>
      ) : loading ? (
        <p className="px-3 py-6 text-center text-xs text-faint">Loading watchlist…</p>
      ) : rows.length === 0 ? (
        <p className="px-3 py-6 text-center text-xs text-faint">
          No tickers watched. Add one to start streaming it.
        </p>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <table className="w-full border-collapse text-sm">
            <caption className="sr-only">
              Watched tickers with live prices and daily change
            </caption>
            <thead className="sticky top-0 bg-panel">
              <tr className="text-[10px] tracking-[0.12em] text-faint uppercase">
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Symbol
                </th>
                <th scope="col" className="py-1.5 text-left font-medium">
                  Trend
                </th>
                <th scope="col" className="px-2 py-1.5 text-right font-medium">
                  Last
                </th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">
                  Day
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <TickerRow
                  key={row.ticker}
                  ticker={row.ticker}
                  quote={prices[row.ticker] ?? row.quote}
                  points={sparklines[row.ticker] ?? []}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

interface TickerRowProps {
  ticker: string;
  /**
   * The streamed quote, falling back to the one `GET /api/watchlist` returned,
   * and null for a ticker the source has not priced yet — which is normal for
   * one poll interval after an add on Massive. Renders as a dash, never zero.
   */
  quote: Quote | null;
  points: number[];
}

export function TickerRow({ ticker, quote, points }: TickerRowProps) {
  const flash = usePriceFlash(quote?.price ?? null);

  return (
    <tr className="border-t border-grid hover:bg-raised" data-testid={`row-${ticker}`}>
      <th scope="row" className="px-3 py-1.5 text-left font-mono text-xs font-semibold text-ink">
        {ticker}
      </th>

      <td className="py-1.5 align-middle">
        <Sparkline points={points} label={`${ticker} price since page load`} />
      </td>

      {/* `key` restarts the animation when a second move lands inside the
          500 ms window — see usePriceFlash. */}
      <td
        key={flash.seq}
        className={`px-2 py-1.5 text-right font-mono text-xs text-ink ${flashClass(flash)}`}
        data-testid={`price-${ticker}`}
      >
        {quote === null ? EM_DASH : formatMoney(quote.price)}
      </td>

      <td
        className={`px-3 py-1.5 text-right font-mono text-xs ${toneClass(quote?.day_change_percent)}`}
        data-testid={`day-${ticker}`}
      >
        {formatPercent(quote?.day_change_percent)}
      </td>
    </tr>
  );
}
