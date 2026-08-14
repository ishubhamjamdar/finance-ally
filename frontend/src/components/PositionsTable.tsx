"use client";

/**
 * The positions table — PLAN.md §10: ticker, quantity, average cost, current
 * price, unrealised P&L and % change.
 *
 * Every figure past the average cost is marked against the *stream*, not
 * against the numbers `GET /api/portfolio` returned, so the table moves with
 * the prices instead of freezing between trades. `markPositions` is the one
 * implementation of that; this component only lays it out.
 *
 * A holding the feed cannot price keeps its quantity and its cost and renders
 * dashes for the rest. It is never shown as a zero and never quietly dropped —
 * a position missing from the table is indistinguishable from one that was
 * sold.
 */

import { flashClass, usePriceFlash } from "@/hooks/usePriceFlash";
import {
  formatDollars,
  formatMoney,
  formatPercent,
  formatQuantity,
  formatSignedDollars,
  toneClass,
} from "@/lib/format";
import type { LivePosition } from "@/lib/valuation";

interface PositionsTableProps {
  positions: LivePosition[];
  /** Highlights the row for the charted ticker. */
  selected?: string | null;
  onSelect?: (ticker: string) => void;
  loading?: boolean;
}

export function PositionsTable({
  positions,
  selected = null,
  onSelect,
  loading = false,
}: PositionsTableProps) {
  return (
    <section
      className="flex h-full min-h-0 flex-col rounded border border-edge bg-panel"
      aria-labelledby="positions-heading"
    >
      <header className="flex items-baseline justify-between border-b border-edge px-3 py-2">
        <h2
          id="positions-heading"
          className="text-[11px] font-semibold tracking-[0.14em] text-muted uppercase"
        >
          Positions
        </h2>
        <span className="font-mono text-[11px] text-faint">{positions.length}</span>
      </header>

      {positions.length === 0 ? (
        <p className="px-3 py-6 text-center text-xs text-faint">
          {loading ? "Loading positions…" : "No positions. Buy something from the trade bar."}
        </p>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <table className="w-full border-collapse text-sm">
            <caption className="sr-only">Open positions marked to live prices</caption>
            <thead className="sticky top-0 bg-panel">
              <tr className="text-[10px] tracking-[0.12em] text-faint uppercase">
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Symbol
                </th>
                <th scope="col" className="px-2 py-1.5 text-right font-medium">
                  Qty
                </th>
                <th scope="col" className="px-2 py-1.5 text-right font-medium">
                  Avg cost
                </th>
                <th scope="col" className="px-2 py-1.5 text-right font-medium">
                  Last
                </th>
                <th scope="col" className="px-2 py-1.5 text-right font-medium">
                  Value
                </th>
                <th scope="col" className="px-2 py-1.5 text-right font-medium">
                  P&amp;L
                </th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">
                  %
                </th>
              </tr>
            </thead>
            <tbody>
              {positions.map((position) => (
                <PositionRow
                  key={position.ticker}
                  position={position}
                  selected={position.ticker === selected}
                  onSelect={onSelect}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function PositionRow({
  position,
  selected = false,
  onSelect,
}: {
  position: LivePosition;
  selected?: boolean;
  onSelect?: (ticker: string) => void;
}) {
  const flash = usePriceFlash(position.price);

  return (
    <tr
      className={`border-t border-grid ${selected ? "bg-raised" : "hover:bg-raised"} ${
        onSelect === undefined ? "" : "cursor-pointer"
      }`}
      data-testid={`position-${position.ticker}`}
      data-selected={selected ? "true" : undefined}
      onClick={onSelect === undefined ? undefined : () => onSelect(position.ticker)}
    >
      <th scope="row" className="px-3 py-1.5 text-left font-mono text-xs font-semibold text-ink">
        {onSelect === undefined ? (
          position.ticker
        ) : (
          <button
            type="button"
            // The row is clickable for the mouse; this is what makes the same
            // action reachable from the keyboard, without nesting interactive
            // elements in every cell.
            className="cursor-pointer hover:text-brand focus-visible:text-brand"
            onClick={(event) => {
              event.stopPropagation();
              onSelect(position.ticker);
            }}
          >
            {position.ticker}
          </button>
        )}
      </th>

      <td className="px-2 py-1.5 text-right font-mono text-xs text-muted">
        {formatQuantity(position.quantity)}
      </td>
      <td className="px-2 py-1.5 text-right font-mono text-xs text-muted">
        {formatMoney(position.avgCost)}
      </td>
      <td
        key={flash.seq}
        className={`px-2 py-1.5 text-right font-mono text-xs text-ink ${flashClass(flash)}`}
        data-testid={`position-price-${position.ticker}`}
      >
        {formatMoney(position.price)}
      </td>
      <td className="px-2 py-1.5 text-right font-mono text-xs text-ink">
        {formatDollars(position.marketValue)}
      </td>
      <td
        className={`px-2 py-1.5 text-right font-mono text-xs ${toneClass(position.unrealizedPnl)}`}
        data-testid={`position-pnl-${position.ticker}`}
      >
        {formatSignedDollars(position.unrealizedPnl)}
      </td>
      <td
        className={`px-3 py-1.5 text-right font-mono text-xs ${toneClass(position.unrealizedPnlPercent)}`}
      >
        {formatPercent(position.unrealizedPnlPercent)}
      </td>
    </tr>
  );
}
