"use client";

/**
 * The main chart — PLAN.md §10: the selected ticker, larger, price over time.
 *
 * The series is the same one the sparklines are drawn from: accumulated on the
 * client from the SSE stream since page load, because there is no historical
 * price endpoint in this system and PLAN.md §2 says so outright. Nothing is
 * back-filled or interpolated to make the chart look older than it is; a
 * ticker that has just been added draws one point and grows.
 *
 * The header carries the numbers you cannot read off a line — the last price,
 * the change since the previous close, and the range of the window — so the
 * chart is legible without a crosshair.
 */

import { LineChart } from "@/components/LineChart";
import { flashClass, usePriceFlash } from "@/hooks/usePriceFlash";
import { EM_DASH, formatClock, formatMoney, formatPercent, toneClass } from "@/lib/format";
import type { Quote } from "@/lib/types";

interface PriceChartProps {
  /** Null before the user has picked one and when the watchlist is empty. */
  ticker: string | null;
  /** The live quote, or null for a ticker the source has not priced. */
  quote: Quote | null;
  /** Prices since page load, oldest first. */
  points: number[];
}

export function PriceChart({ ticker, quote, points }: PriceChartProps) {
  const flash = usePriceFlash(quote?.price ?? null);
  const window = points.filter((point) => Number.isFinite(point));
  const low = window.length === 0 ? null : Math.min(...window);
  const high = window.length === 0 ? null : Math.max(...window);

  // Green or red for the whole plot, measured across exactly what is drawn.
  //
  // That is deliberately *not* the same span as the watchlist sparkline, which
  // slices the last `MAX_SPARKLINE_POINTS` of the same buffer. A ticker down
  // over five minutes but up over the last one draws a red chart beside a
  // green sparkline, and both are right: each line's colour describes the line
  // it is on, and the chart says how long its window is underneath.
  const rising = window.length < 2 || window[window.length - 1] >= window[0];

  return (
    <section
      className="flex min-h-0 flex-col rounded border border-edge bg-panel"
      aria-labelledby="chart-heading"
    >
      <header className="flex flex-wrap items-baseline gap-x-6 gap-y-1 border-b border-edge px-3 py-2">
        <h2 id="chart-heading" className="flex items-baseline gap-2">
          <span className="font-mono text-sm font-semibold text-ink" data-testid="chart-ticker">
            {ticker ?? EM_DASH}
          </span>
          <span className="text-[11px] tracking-[0.14em] text-faint uppercase">Price</span>
        </h2>

        <span
          key={flash.seq}
          className={`font-mono text-lg leading-none text-ink ${flashClass(flash)}`}
          data-testid="chart-price"
        >
          {quote === null ? EM_DASH : formatMoney(quote.price)}
        </span>

        <span
          className={`font-mono text-xs ${toneClass(quote?.day_change_percent)}`}
          data-testid="chart-day"
        >
          {formatPercent(quote?.day_change_percent)}
        </span>

        <dl className="ml-auto flex gap-4 font-mono text-[10px] text-faint">
          <div className="flex gap-1.5">
            <dt className="tracking-[0.12em] uppercase">Low</dt>
            <dd className="text-muted" data-testid="chart-low">
              {formatMoney(low)}
            </dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="tracking-[0.12em] uppercase">High</dt>
            <dd className="text-muted" data-testid="chart-high">
              {formatMoney(high)}
            </dd>
          </div>
        </dl>
      </header>

      <div className="flex min-h-0 flex-1 flex-col p-3">
        <LineChart
          values={window}
          tone={rising ? "text-up" : "text-down"}
          format={formatMoney}
          label={`${ticker ?? "No ticker"} price since page load`}
          empty={
            ticker === null
              ? "Select a ticker in the watchlist to chart it."
              : `Waiting for ${ticker} prices. The chart fills in from the stream — there is no history behind it.`
          }
          from={window.length === 0 ? undefined : `${window.length} pts since page load`}
          to={quote === null ? undefined : formatClock(quote.timestamp)}
          testId="price-chart"
        />
      </div>
    </section>
  );
}
