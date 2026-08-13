"use client";

/**
 * The watchlist grid — PLAN.md §10: symbol, live price flashing on change,
 * daily change %, and a sparkline accumulated from the stream. Clicking a row
 * charts that ticker, and the panel owns the add/remove controls PLAN.md §2
 * promises ("manage the watchlist manually or via the AI chat").
 *
 * Row order is the order `GET /api/watchlist` returned, which is add order.
 * Sorting by price or change would reshuffle the grid twice a second and make
 * it unreadable — the backend endpoint documents the same reasoning.
 *
 * The mutations are handed in rather than performed here, because what has to
 * happen after one is not local: `POST /api/watchlist` changes what the header
 * and the heatmap should show, and only the provider knows how to re-read
 * them. This component owns the form, the pending state and the error text;
 * `TerminalProvider` owns the call and the refresh.
 */

import { useState } from "react";
import type { FormEvent } from "react";

import { Sparkline } from "@/components/Sparkline";
import { flashClass, usePriceFlash } from "@/hooks/usePriceFlash";
import { describeError } from "@/lib/api";
import { EM_DASH, formatMoney, formatPercent, toneClass } from "@/lib/format";
import type { Quote, WatchlistRow } from "@/lib/types";

interface WatchlistPanelProps {
  rows: WatchlistRow[];
  prices: Record<string, Quote>;
  sparklines: Record<string, number[]>;
  loading?: boolean;
  error?: string | null;
  /** The charted ticker, highlighted here. */
  selected?: string | null;
  onSelect?: (ticker: string) => void;
  /** Rejects with the backend's reason — 409 duplicate, 400 list full, 503 no feed. */
  onAdd?: (ticker: string) => Promise<void>;
  onRemove?: (ticker: string) => Promise<void>;
}

export function WatchlistPanel({
  rows,
  prices,
  sparklines,
  loading = false,
  error = null,
  selected = null,
  onSelect,
  onAdd,
  onRemove,
}: WatchlistPanelProps) {
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [mutationError, setMutationError] = useState<string | null>(null);

  async function run(action: () => Promise<void>) {
    setPending(true);
    setMutationError(null);
    try {
      await action();
    } catch (cause: unknown) {
      setMutationError(describeError(cause));
    } finally {
      setPending(false);
    }
  }

  async function add(event: FormEvent) {
    event.preventDefault();
    if (onAdd === undefined || pending) return;

    const ticker = draft.trim().toUpperCase();
    if (ticker === "") {
      setMutationError("Enter a ticker to add.");
      return;
    }

    // Cleared only on success: a rejected symbol stays in the field so the
    // user can correct a typo rather than retype it.
    await run(async () => {
      await onAdd(ticker);
      setDraft("");
    });
  }

  return (
    <section
      className="flex min-h-0 flex-col rounded border border-edge bg-panel"
      aria-labelledby="watchlist-heading"
    >
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-edge px-3 py-2">
        <h2
          id="watchlist-heading"
          className="text-[11px] font-semibold tracking-[0.14em] text-muted uppercase"
        >
          Watchlist
          <span className="ml-2 font-mono text-[11px] font-normal text-faint">{rows.length}</span>
        </h2>

        {onAdd !== undefined && (
          <form className="flex items-center gap-1" onSubmit={add}>
            <input
              value={draft}
              onChange={(event) => setDraft(event.target.value.toUpperCase())}
              placeholder="Add ticker"
              aria-label="Add ticker"
              autoComplete="off"
              spellCheck={false}
              className="w-24 rounded border border-edge bg-raised px-2 py-0.5 font-mono text-xs text-ink uppercase placeholder:text-faint focus:border-brand focus:outline-none"
            />
            <button
              type="submit"
              disabled={pending}
              aria-label="Add to watchlist"
              className="rounded bg-submit px-2 py-0.5 text-xs font-semibold text-ink hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
            >
              +
            </button>
          </form>
        )}
      </header>

      {mutationError !== null && (
        <p
          className="border-b border-edge px-3 py-1.5 text-xs text-down"
          role="alert"
          data-testid="watchlist-error"
        >
          {mutationError}
        </p>
      )}

      {/* An error with rows on screen is a banner, not a replacement.
          `useApiResource` keeps the last good data through a failed reload
          precisely so the grid survives one, and those rows are still being
          marked by the stream — throwing ten live prices away to show one line
          of red text would lose more than it explains. */}
      {error !== null && rows.length > 0 && (
        <p className="border-b border-edge px-3 py-1.5 text-xs text-down" role="alert">
          {error} — showing the last known list.
        </p>
      )}

      {error !== null && rows.length === 0 ? (
        <p className="px-3 py-6 text-center text-xs text-down">{error}</p>
      ) : rows.length === 0 && loading ? (
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
                <th scope="col" className="px-2 py-1.5 text-right font-medium">
                  Day
                </th>
                {onRemove !== undefined && <th scope="col" className="w-6 px-1 py-1.5" />}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <TickerRow
                  key={row.ticker}
                  ticker={row.ticker}
                  quote={prices[row.ticker] ?? row.quote}
                  points={sparklines[row.ticker] ?? []}
                  selected={row.ticker === selected}
                  onSelect={onSelect}
                  onRemove={
                    onRemove === undefined ? undefined : () => run(() => onRemove(row.ticker))
                  }
                  busy={pending}
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
  selected?: boolean;
  onSelect?: (ticker: string) => void;
  onRemove?: () => void;
  busy?: boolean;
}

export function TickerRow({
  ticker,
  quote,
  points,
  selected = false,
  onSelect,
  onRemove,
  busy = false,
}: TickerRowProps) {
  const flash = usePriceFlash(quote?.price ?? null);

  return (
    <tr
      className={`border-t border-grid ${selected ? "bg-raised" : "hover:bg-raised"} ${
        onSelect === undefined ? "" : "cursor-pointer"
      }`}
      data-testid={`row-${ticker}`}
      data-selected={selected ? "true" : undefined}
      onClick={onSelect === undefined ? undefined : () => onSelect(ticker)}
    >
      <th scope="row" className="px-3 py-1.5 text-left font-mono text-xs font-semibold text-ink">
        {onSelect === undefined ? (
          ticker
        ) : (
          // The whole row is clickable for the mouse; the symbol is the
          // keyboard route to the same action, without an interactive element
          // in every cell.
          <button
            type="button"
            className={`cursor-pointer hover:text-brand focus-visible:text-brand ${
              selected ? "text-brand" : ""
            }`}
            onClick={(event) => {
              event.stopPropagation();
              onSelect(ticker);
            }}
          >
            {ticker}
          </button>
        )}
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
        className={`px-2 py-1.5 text-right font-mono text-xs ${toneClass(quote?.day_change_percent)}`}
        data-testid={`day-${ticker}`}
      >
        {formatPercent(quote?.day_change_percent)}
      </td>

      {onRemove !== undefined && (
        <td className="px-1 py-1.5 text-right">
          <button
            type="button"
            disabled={busy}
            aria-label={`Remove ${ticker} from the watchlist`}
            title="Remove — does not sell a position"
            className="cursor-pointer px-1 text-xs text-faint hover:text-down disabled:cursor-not-allowed disabled:opacity-50"
            onClick={(event) => {
              event.stopPropagation();
              onRemove();
            }}
          >
            ×
          </button>
        </td>
      )}
    </tr>
  );
}
