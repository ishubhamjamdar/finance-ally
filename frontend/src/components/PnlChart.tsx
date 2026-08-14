"use client";

/**
 * Total portfolio value over time — PLAN.md §10, drawn from
 * `portfolio_snapshots` via `GET /api/portfolio/history`.
 *
 * The series is what the backend *recorded*, never recomputed from today's
 * prices, and it is not extended on the client with a live point either. Both
 * would draw a line the account never traced. It grows in two ways, which
 * between them cover everything the user can see happen: the backend's
 * 30-second snapshot task, picked up by the poll behind `useAccount`, and a
 * trade, which writes a snapshot immediately and refreshes this series with it.
 *
 * PLAN.md §7 is worth knowing here: a snapshot is *skipped* while a held
 * ticker has no price, so a gap in the line is deliberate and honest. Filling
 * one in would draw a drawdown the account never suffered.
 */

import { LineChart } from "@/components/LineChart";
import { formatDollarsCompact, formatPercent, formatSignedDollars, toneClass } from "@/lib/format";
import type { Snapshot } from "@/lib/types";

interface PnlChartProps {
  /** Oldest first, as `GET /api/portfolio/history` returns them. */
  snapshots: Snapshot[];
  loading?: boolean;
  error?: string | null;
}

export function PnlChart({ snapshots, loading = false, error = null }: PnlChartProps) {
  const values = snapshots
    .map((snapshot) => snapshot.total_value)
    .filter((value) => Number.isFinite(value));

  const first = values.at(0) ?? null;
  const last = values.at(-1) ?? null;
  const change = first === null || last === null ? null : last - first;
  const changePercent = change === null || first === 0 ? null : (change / (first as number)) * 100;

  return (
    <section
      className="flex h-full min-h-0 flex-col rounded border border-edge bg-panel"
      aria-labelledby="pnl-heading"
    >
      <header className="flex flex-wrap items-baseline gap-x-4 gap-y-1 border-b border-edge px-3 py-2">
        <h2
          id="pnl-heading"
          className="text-[11px] font-semibold tracking-[0.14em] text-muted uppercase"
        >
          Portfolio value
        </h2>
        {change !== null && (
          <span className={`font-mono text-xs ${toneClass(change)}`} data-testid="pnl-change">
            {formatSignedDollars(change)} {formatPercent(changePercent)}
          </span>
        )}
        <span className="ml-auto font-mono text-[10px] text-faint" data-testid="pnl-count">
          {snapshots.length} snapshots
        </span>
      </header>

      {/* Same rule as the watchlist: a failed reload keeps the series that is
          already drawn and says so above it, rather than blanking a chart the
          user was reading. */}
      {error !== null && values.length > 0 && (
        <p className="border-b border-edge px-3 py-1.5 text-xs text-down" role="alert">
          {error} — showing the last series received.
        </p>
      )}

      <div className="flex min-h-0 flex-1 flex-col p-3">
        <LineChart
          values={values}
          tone={change !== null && change < 0 ? "text-down" : "text-up"}
          format={formatDollarsCompact}
          label="Portfolio value over time"
          empty={
            error !== null
              ? error
              : loading
                ? "Loading the value series…"
                : "No snapshots yet. The first is recorded within 30 seconds, and a trade writes one immediately."
          }
          from={first === null ? undefined : formatStamp(snapshots[0].recorded_at)}
          to={last === null ? undefined : formatStamp(snapshots[snapshots.length - 1].recorded_at)}
          testId="pnl-chart"
        />
      </div>
    </section>
  );
}

/**
 * `recorded_at` as `app/db/database.py::utc_now` writes it —
 * `datetime.now(timezone.utc).isoformat()`, so `…+00:00` and already
 * unambiguous.
 *
 * The guard is for a row that is not: a bare ISO string with no zone is read
 * by `Date` as *local* time, which silently shifts the whole axis by the
 * viewer's offset. Appending `Z` when nothing else says otherwise costs
 * nothing and keeps the failure impossible rather than rare.
 */
export function formatStamp(recordedAt: string): string {
  const normalised = /(Z|[+-]\d{2}:?\d{2})$/.test(recordedAt) ? recordedAt : `${recordedAt}Z`;
  const parsed = new Date(normalised);
  if (Number.isNaN(parsed.getTime())) return recordedAt;
  return parsed.toLocaleTimeString("en-US", { hour12: false });
}
