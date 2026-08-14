"use client";

/**
 * The trade bar — PLAN.md §10: ticker, quantity, buy and sell. Market orders,
 * instant fill, no confirmation dialog.
 *
 * ## What this component is careful about
 *
 * **The rejection is the feature.** PLAN.md §8 draws a line between a 422 —
 * the request was malformed — and a 400, which means the request was fine and
 * the account could not support it: no price, a frozen price, not enough cash,
 * more shares than are held. Both come back with the backend's own wording,
 * and a trade that fails has to *say so* on screen. A silent failure here is
 * the user believing they own something they do not.
 *
 * **The ticker field follows the chart until it is typed in.** Selecting a row
 * fills the field, so the common case is quantity plus a button. Once the user
 * edits it the field is theirs and stays theirs — including across a fill,
 * because the symbol that just traded is the one the next order is most likely
 * meant for, and silently swapping it back would send that order elsewhere.
 *
 * **No client-side money rules.** Whether the cash covers it is the server's
 * question — it holds the balance and the fill price, and this component holds
 * a stale copy of neither. All that is validated here is that the fields are
 * filled in at all, so a fat-fingered form does not cost a round trip.
 */

import { useState } from "react";
import type { FormEvent } from "react";

import { describeError } from "@/lib/api";
import { formatDollars, formatMoney, formatQuantity } from "@/lib/format";
import { isTicker, normalizeTicker } from "@/lib/ticker";
import type { TradeFill, TradeOrder, TradeSide } from "@/lib/types";

interface TradeBarProps {
  /**
   * Executes the order. Resolves with the fill, rejects with the backend's
   * reason — `ApiError` where the server answered, anything else where it did
   * not.
   */
  onSubmit: (order: TradeOrder) => Promise<TradeFill>;
  /** The charted ticker. Pre-fills the field until the user types over it. */
  selected?: string | null;
}

export function TradeBar({ onSubmit, selected = null }: TradeBarProps) {
  // Null means "follow the selection". An empty string is a field the user has
  // deliberately cleared, and must not spring back to the charted ticker.
  const [typedTicker, setTypedTicker] = useState<string | null>(null);
  const [quantity, setQuantity] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fill, setFill] = useState<TradeFill | null>(null);

  const ticker = typedTicker ?? selected ?? "";

  async function submit(side: TradeSide, event: FormEvent) {
    event.preventDefault();
    if (pending) return;

    const symbol = normalizeTicker(ticker);
    const size = Number(quantity);

    if (symbol === "") {
      setFill(null);
      setError("Enter a ticker.");
      return;
    }
    // Shape only — whether the symbol *exists* and whether the account can
    // support the order are both the server's to answer. This is here so a
    // typo comes back as a sentence rather than as FastAPI's field report.
    if (!isTicker(symbol)) {
      setFill(null);
      setError(`“${symbol}” is not a ticker symbol.`);
      return;
    }
    if (!Number.isFinite(size) || size <= 0) {
      setFill(null);
      setError("Enter a quantity greater than zero.");
      return;
    }

    setPending(true);
    setError(null);
    setFill(null);
    try {
      const executed = await onSubmit({ ticker: symbol, side, quantity: size });
      setFill(executed);
      // Only the quantity clears. The ticker stays exactly as it was sent:
      // reverting it to the charted symbol would mean a user who typed NVDA,
      // sold it, then typed a new quantity and clicked Sell again would sell
      // *AAPL* — a second order against a different holding, with no
      // confirmation dialog anywhere in this path to catch it.
      setQuantity("");
    } catch (cause: unknown) {
      setError(describeError(cause));
    } finally {
      setPending(false);
    }
  }

  return (
    <section
      className="rounded border border-edge bg-panel px-3 py-2"
      aria-labelledby="trade-heading"
    >
      <div className="flex flex-wrap items-end gap-3">
        <h2
          id="trade-heading"
          className="text-[11px] font-semibold tracking-[0.14em] text-muted uppercase"
        >
          Trade
        </h2>

        <form className="flex flex-wrap items-end gap-2" onSubmit={(event) => submit("buy", event)}>
          <label className="flex flex-col gap-1">
            <span className="text-[10px] tracking-[0.12em] text-faint uppercase">Ticker</span>
            <input
              value={ticker}
              onChange={(event) => setTypedTicker(event.target.value.toUpperCase())}
              placeholder="AAPL"
              autoComplete="off"
              spellCheck={false}
              aria-label="Ticker"
              className="w-24 rounded border border-edge bg-raised px-2 py-1 font-mono text-sm text-ink uppercase placeholder:text-faint focus:border-brand focus:outline-none"
            />
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-[10px] tracking-[0.12em] text-faint uppercase">Quantity</span>
            <input
              value={quantity}
              onChange={(event) => setQuantity(event.target.value)}
              // `type="number"` rather than a pattern, so phones offer digits
              // and the browser rejects letters before this component has to.
              // `step="any"` because shares are fractional here.
              type="number"
              min="0"
              step="any"
              placeholder="10"
              autoComplete="off"
              aria-label="Quantity"
              className="w-24 rounded border border-edge bg-raised px-2 py-1 font-mono text-sm text-ink placeholder:text-faint focus:border-brand focus:outline-none"
            />
          </label>

          <button
            type="submit"
            disabled={pending}
            className="rounded bg-submit px-4 py-1.5 text-xs font-semibold tracking-wide text-ink uppercase hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Buy
          </button>

          <button
            type="button"
            disabled={pending}
            onClick={(event) => submit("sell", event)}
            className="rounded border border-down px-4 py-1.5 text-xs font-semibold tracking-wide text-down uppercase hover:bg-down/10 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Sell
          </button>
        </form>

        <p className="min-w-0 flex-1 text-right text-xs" data-testid="trade-status">
          {pending ? (
            <span className="text-muted">Sending order…</span>
          ) : error !== null ? (
            <span className="text-down" role="alert">
              {error}
            </span>
          ) : fill !== null ? (
            <span className="text-up" role="status">
              {fill.side === "buy" ? "Bought" : "Sold"} {formatQuantity(fill.quantity)}{" "}
              {fill.ticker} at {formatMoney(fill.price)} — {formatDollars(fill.value)}
            </span>
          ) : (
            <span className="text-faint">Market orders, filled at the live price.</span>
          )}
        </p>
      </div>
    </section>
  );
}
