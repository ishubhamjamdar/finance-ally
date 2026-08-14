"use client";

/**
 * The portfolio heatmap — PLAN.md §10: a treemap where each rectangle is a
 * position, sized by portfolio weight and coloured by P&L.
 *
 * Weight is share of the *marked* positions' value, so the tiles always fill
 * the box. Colour is the sign of unrealised P&L with the intensity carrying
 * the magnitude, which is what makes the picture readable at a glance: a big
 * pale green tile is a large holding barely up, a small bright red one is a
 * small holding badly down.
 *
 * A holding with no price gets no tile. It cannot be sized — there is no
 * market value to size it by — and giving it one from its cost would put a
 * rectangle of one unit beside rectangles of another. Those tickers are named
 * under the map instead, which is the same thing the header does with them.
 */

import { formatPercent, formatSignedDollars, EM_DASH } from "@/lib/format";
import { squarify } from "@/lib/treemap";
import type { LivePosition } from "@/lib/valuation";

/**
 * The P&L percentage at which a tile is fully saturated. Past it the colour
 * stops carrying information, so it clamps rather than running to white.
 */
const FULL_INTENSITY_PERCENT = 8;

/** Below this share of the box, a tile has no room for anything but its symbol. */
const LABEL_THRESHOLD = 0.045;

interface PortfolioHeatmapProps {
  positions: LivePosition[];
  selected?: string | null;
  onSelect?: (ticker: string) => void;
  loading?: boolean;
}

export function PortfolioHeatmap({
  positions,
  selected = null,
  onSelect,
  loading = false,
}: PortfolioHeatmapProps) {
  const tiles = squarify(positions, (position) => position.weight);
  const unpriced = positions.filter((position) => position.marketValue === null);

  return (
    <section
      className="flex h-full min-h-0 flex-col rounded border border-edge bg-panel"
      aria-labelledby="heatmap-heading"
    >
      <header className="flex items-baseline justify-between border-b border-edge px-3 py-2">
        <h2
          id="heatmap-heading"
          className="text-[11px] font-semibold tracking-[0.14em] text-muted uppercase"
        >
          Allocation
        </h2>
        <span className="text-[10px] text-faint">sized by weight · coloured by P&amp;L</span>
      </header>

      {tiles.length === 0 ? (
        <p className="px-3 py-6 text-center text-xs text-faint" data-testid="heatmap-empty">
          {loading
            ? "Loading positions…"
            : unpriced.length > 0
              ? "Nothing can be sized yet — no held ticker has a price."
              : "No positions yet. The map fills in as you buy."}
        </p>
      ) : (
        <div className="relative min-h-[9rem] flex-1 p-1" data-testid="heatmap">
          {tiles.map(({ item, x, y, width, height }) => (
            <Tile
              key={item.ticker}
              position={item}
              selected={item.ticker === selected}
              onSelect={onSelect}
              style={{
                left: `${percent(x)}%`,
                top: `${percent(y)}%`,
                width: `${percent(width)}%`,
                height: `${percent(height)}%`,
              }}
              roomy={width * height >= LABEL_THRESHOLD}
            />
          ))}
        </div>
      )}

      {unpriced.length > 0 && (
        <p className="border-t border-edge px-3 py-1.5 text-[11px] text-accent" data-testid="heatmap-unpriced">
          No price yet, so not mapped: {unpriced.map((position) => position.ticker).join(" ")}
        </p>
      )}
    </section>
  );
}

function Tile({
  position,
  selected,
  onSelect,
  style,
  roomy,
}: {
  position: LivePosition;
  selected: boolean;
  onSelect?: (ticker: string) => void;
  style: React.CSSProperties;
  roomy: boolean;
}) {
  const pnl = position.unrealizedPnlPercent;
  const intensity =
    pnl === null ? 0 : Math.min(Math.abs(pnl) / FULL_INTENSITY_PERCENT, 1) * 0.65 + 0.15;
  const colour = pnl === null || pnl === 0 ? "var(--color-edge)" : pnl > 0 ? "var(--color-up)" : "var(--color-down)";

  const label = `${position.ticker}, ${formatPercent(position.weight === null ? null : position.weight * 100)} of positions, ${formatPercent(pnl)}`;

  return (
    <button
      type="button"
      className={`absolute overflow-hidden rounded-[3px] p-1 text-left transition-[outline-color] ${
        selected ? "outline-2 outline-accent" : "outline-1 outline-terminal"
      } ${onSelect === undefined ? "cursor-default" : "cursor-pointer"}`}
      style={{
        ...style,
        backgroundColor:
          pnl === null || pnl === 0
            ? colour
            : `color-mix(in srgb, ${colour} ${Math.round(intensity * 100)}%, var(--color-panel))`,
      }}
      aria-label={label}
      title={label}
      data-testid={`tile-${position.ticker}`}
      data-selected={selected ? "true" : undefined}
      onClick={onSelect === undefined ? undefined : () => onSelect(position.ticker)}
    >
      <span className="block truncate font-mono text-[11px] leading-tight font-semibold text-ink">
        {position.ticker}
      </span>
      {roomy && (
        <>
          <span className="block truncate font-mono text-[10px] leading-tight text-ink/80">
            {formatPercent(pnl)}
          </span>
          <span className="block truncate font-mono text-[10px] leading-tight text-ink/60">
            {position.unrealizedPnl === null ? EM_DASH : formatSignedDollars(position.unrealizedPnl)}
          </span>
        </>
      )}
    </button>
  );
}

/** Layout comes back in a unit box; CSS wants percentages. */
function percent(fraction: number): number {
  return Math.round(fraction * 10000) / 100;
}
