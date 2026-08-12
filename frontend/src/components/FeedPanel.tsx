"use client";

/**
 * Feed health and notable moves.
 *
 * Two things live here. The first is what the stream is actually doing —
 * frames received, when the last one landed, how many tickers are priced —
 * which is the difference between "the market is quiet" and "the connection
 * died four minutes ago and every price on screen is stale".
 *
 * The second is the `event: shock` frames. `backend/app/market/` has published
 * them since Checkpoint 1 and nothing consumed them; they are the simulator's
 * 2-5% moves, and a trading terminal is where they belong.
 */

import type { ConnectionStatus } from "@/hooks/usePriceStream";
import { EM_DASH, formatClock, formatMoney, formatPercent, toneClass } from "@/lib/format";
import type { MarketShock } from "@/lib/types";

const STATUS_DETAIL: Record<ConnectionStatus, string> = {
  connecting: "Opening the price stream",
  connected: "Streaming",
  reconnecting: "Connection lost — the browser is retrying",
  disconnected: "No price stream. Prices below are the last received.",
};

interface FeedPanelProps {
  status: ConnectionStatus;
  frames: number;
  lastFrameAt: number | null;
  tickerCount: number;
  shocks: MarketShock[];
}

export function FeedPanel({
  status,
  frames,
  lastFrameAt,
  tickerCount,
  shocks,
}: FeedPanelProps) {
  return (
    <section
      className="flex min-h-0 flex-col rounded border border-edge bg-panel"
      aria-labelledby="feed-heading"
    >
      <header className="flex items-baseline justify-between border-b border-edge px-3 py-2">
        <h2
          id="feed-heading"
          className="text-[11px] font-semibold tracking-[0.14em] text-muted uppercase"
        >
          Market Feed
        </h2>
        <span
          className={`text-[11px] ${status === "disconnected" ? "text-down" : "text-faint"}`}
          data-testid="feed-detail"
        >
          {STATUS_DETAIL[status]}
        </span>
      </header>

      <dl className="grid grid-cols-3 gap-px border-b border-edge bg-edge">
        <Metric label="Frames" value={frames === 0 ? EM_DASH : String(frames)} />
        <Metric
          label="Last update"
          value={lastFrameAt === null ? EM_DASH : formatClock(lastFrameAt / 1000)}
        />
        <Metric label="Tickers priced" value={tickerCount === 0 ? EM_DASH : String(tickerCount)} />
      </dl>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <h3 className="px-3 pt-3 pb-1 text-[10px] tracking-[0.12em] text-faint uppercase">
          Notable moves
        </h3>
        {shocks.length === 0 ? (
          <p className="px-3 py-3 text-xs text-faint">
            Nothing yet. Sharp moves appear here as they happen.
          </p>
        ) : (
          <ul className="divide-y divide-grid">
            {shocks.map((shock) => (
              <li
                key={`${shock.ticker}-${shock.timestamp}`}
                className="flex items-baseline gap-3 px-3 py-1.5 font-mono text-xs"
              >
                <span className="w-14 font-semibold text-ink">{shock.ticker}</span>
                <span className={`w-16 text-right ${toneClass(shock.magnitude_percent)}`}>
                  {formatPercent(shock.magnitude_percent)}
                </span>
                <span className="w-20 text-right text-muted">{formatMoney(shock.price)}</span>
                <span className="ml-auto text-faint">{formatClock(shock.timestamp)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-panel px-3 py-2">
      <dt className="text-[10px] tracking-[0.12em] text-faint uppercase">{label}</dt>
      <dd className="font-mono text-sm text-ink">{value}</dd>
    </div>
  );
}
