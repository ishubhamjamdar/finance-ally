"use client";

/**
 * The chart both time series in this workstation are drawn with: the selected
 * ticker's price, and the portfolio's value over time.
 *
 * ## Why this is SVG and not a charting library
 *
 * PLAN.md §10 prefers a canvas library "for performance", and the reason it
 * gives is the reason this is fine without one. What arrives at 2 Hz is one
 * new point on one polyline: the render is a string of coordinates and a path
 * substitution, not a scene graph. An imperative canvas library would add a
 * dependency, an instance to dispose of on unmount, and a jsdom shim, to
 * replace a `<path>` React already knows how to reconcile. See the Checkpoint
 * 6 log entry.
 *
 * ## How it scales
 *
 * The plot is drawn in a fixed 0–100 box and stretched to the container with
 * `preserveAspectRatio="none"`, exactly as `Sparkline` does. That is what lets
 * the chart fill a panel of any shape without measuring it — no
 * `ResizeObserver`, nothing to disconnect, and no first render at the wrong
 * size. Strokes carry `vector-effect="non-scaling-stroke"` so the distortion
 * never reaches the line's width, and every piece of *text* is HTML positioned
 * over the box rather than `<text>` inside it, so no label is ever stretched.
 */

import { EM_DASH } from "@/lib/format";

/** The plot's internal coordinate space. Arbitrary; it is stretched to fit. */
const BOX = 100;

/** Where the gridlines and their labels sit, top to bottom. */
const TICK_FRACTIONS = [0, 0.25, 0.5, 0.75, 1];

export interface LineChartProps {
  /** Oldest first. Non-finite entries are dropped rather than drawn as zero. */
  values: readonly number[];
  /** Tailwind text colour driving the line, the fill and the marker. */
  tone?: string;
  /** Renders the axis labels. Given a value in the series' own units. */
  format?: (value: number) => string;
  /** What the chart is, for a screen reader. */
  label: string;
  /** Shown instead of a plot when there is nothing to draw. Never a flat fake line. */
  empty: string;
  /** Bottom-left and bottom-right captions — what the x axis actually spans. */
  from?: string;
  to?: string;
  testId?: string;
}

export function LineChart({
  values,
  tone = "text-brand",
  format = (value) => value.toFixed(2),
  label,
  empty,
  from,
  to,
  testId,
}: LineChartProps) {
  const series = values.filter((value) => Number.isFinite(value));

  if (series.length === 0) {
    return (
      <div
        className="flex flex-1 items-center justify-center px-4 text-center text-xs text-faint"
        data-testid={testId}
      >
        {empty}
      </div>
    );
  }

  const scale = scaleOf(series);
  const step = series.length === 1 ? 0 : BOX / (series.length - 1);
  const at = (index: number, value: number) => ({
    x: series.length === 1 ? BOX / 2 : index * step,
    y: BOX - ((value - scale.min) / scale.span) * BOX,
  });

  const points = series.map((value, index) => at(index, value));
  const path = points.map((point) => `${round(point.x)},${round(point.y)}`).join(" ");
  const last = points[points.length - 1];

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid={testId}>
      <div className="relative min-h-0 flex-1 pr-14">
        {/* The plot area gets its own box, and the `svg` fills it with both
            dimensions given explicitly.

            Sizing the `svg` off the container's insets alone does not work: an
            `svg` is a replaced element with an intrinsic aspect ratio taken
            from its `viewBox` — 1:1 here — so `height: 100%` with no width
            makes the browser compute a *square*. In a wide panel that draws
            the series across 45% of the width with the live-end marker, which
            is positioned in CSS rather than in the viewBox, stranded out to
            the right of where the line stops. */}
        <div className="absolute inset-y-0 right-14 left-0">
          <svg
            className={`h-full w-full ${tone}`}
            viewBox={`0 0 ${BOX} ${BOX}`}
            preserveAspectRatio="none"
            role="img"
            aria-label={`${label}: ${series.length} points, ${format(series[series.length - 1])}`}
          >
            {TICK_FRACTIONS.map((fraction) => (
              <line
                key={fraction}
                x1={0}
                x2={BOX}
                y1={fraction * BOX}
                y2={fraction * BOX}
                stroke="var(--color-grid)"
                strokeWidth={1}
                vectorEffect="non-scaling-stroke"
              />
            ))}

            {/* The fill is what makes a single-pixel line read as a quantity.
              It is closed down to the floor of the box, not to zero: the y
              axis does not start at zero, and pretending it does is how a
              0.4% move gets drawn as a cliff. */}
            <polygon
              points={`0,${BOX} ${path} ${round(last.x)},${BOX}`}
              fill="currentColor"
              opacity={0.12}
            />
            <polyline
              points={path}
              fill="none"
              stroke="currentColor"
              strokeWidth={1.5}
              strokeLinejoin="round"
              strokeLinecap="round"
              vectorEffect="non-scaling-stroke"
            />
          </svg>
        </div>

        {/* The live end of the series. HTML rather than an SVG circle, which
            the horizontal stretch would draw as an ellipse. */}
        <span
          className={`pointer-events-none absolute h-1.5 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-current ${tone}`}
          // The plot is the container minus the 3.5rem label gutter, but a
          // percentage `left` resolves against the whole padding box — so the
          // gutter has to come out of the arithmetic, not out of the units.
          style={{
            left: `calc((100% - 3.5rem) * ${round(last.x / BOX)})`,
            top: `${(last.y / BOX) * 100}%`,
          }}
          data-testid={testId === undefined ? undefined : `${testId}-marker`}
        />

        {TICK_FRACTIONS.map((fraction) => (
          <span
            key={fraction}
            className="pointer-events-none absolute right-0 w-14 -translate-y-1/2 pl-1.5 text-right font-mono text-[10px] text-faint"
            style={{ top: `${fraction * 100}%` }}
          >
            {format(scale.max - fraction * scale.span)}
          </span>
        ))}
      </div>

      {(from !== undefined || to !== undefined) && (
        <div className="flex justify-between pt-1 pr-14 font-mono text-[10px] text-faint">
          <span>{from ?? EM_DASH}</span>
          <span>{to ?? EM_DASH}</span>
        </div>
      )}
    </div>
  );
}

/**
 * The y domain: the series' own range with a little air above and below.
 *
 * A flat series has no range at all, and dividing by it puts every point at
 * `NaN` — the line vanishes rather than being drawn straight, which is the
 * failure that looks like a broken feed. It gets a nominal band around its
 * value instead, so it draws down the middle.
 */
function scaleOf(series: readonly number[]): { min: number; max: number; span: number } {
  const low = Math.min(...series);
  const high = Math.max(...series);
  const range = high - low;
  const padding = range === 0 ? Math.max(Math.abs(high) * 0.01, 0.01) : range * 0.08;

  const min = low - padding;
  const max = high + padding;
  return { min, max, span: max - min };
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}
