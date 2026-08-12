"use client";

/**
 * A price series drawn as an SVG polyline — PLAN.md §2.
 *
 * The points come from `usePriceStream`, accumulated since page load. There is
 * no historical endpoint behind this and nothing is invented to fill the gap:
 * a ticker with one point renders one point, and the line grows as frames
 * arrive. An empty series renders a flat rule rather than nothing, so the
 * column keeps its width and the grid does not jump on the first tick.
 */

interface SparklineProps {
  points: number[];
  width?: number;
  height?: number;
  /** Overrides the up/down colour derived from first-to-last. */
  className?: string;
  label?: string;
}

export function Sparkline({ points, width = 72, height = 22, className, label }: SparklineProps) {
  const usable = points.filter((point) => Number.isFinite(point));

  const tone =
    className ??
    (usable.length < 2 || usable[usable.length - 1] >= usable[0] ? "text-up" : "text-down");

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={tone}
      role="img"
      aria-label={label ?? `${usable.length} price points since page load`}
      preserveAspectRatio="none"
    >
      {usable.length === 0 ? (
        <line
          x1={0}
          y1={height / 2}
          x2={width}
          y2={height / 2}
          stroke="currentColor"
          strokeWidth={1}
          strokeDasharray="2 3"
          opacity={0.25}
        />
      ) : (
        <polyline
          points={project(usable, width, height)}
          fill="none"
          stroke="currentColor"
          strokeWidth={1.25}
          strokeLinejoin="round"
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />
      )}
    </svg>
  );
}

/**
 * Scale the series into the box.
 *
 * Two cases have to be handled or the line disappears: a single point, which
 * has no horizontal extent, and a flat series, where `max - min` is zero and
 * the naive scaling divides by it. Both draw down the middle.
 */
function project(points: number[], width: number, height: number): string {
  const inset = 1.5; // keep the stroke inside the viewBox
  const top = inset;
  const bottom = height - inset;

  if (points.length === 1) {
    const middle = (top + bottom) / 2;
    return `0,${middle} ${width},${middle}`;
  }

  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min;
  const step = width / (points.length - 1);

  return points
    .map((point, index) => {
      const x = index * step;
      const y = span === 0 ? (top + bottom) / 2 : bottom - ((point - min) / span) * (bottom - top);
      return `${round(x)},${round(y)}`;
    })
    .join(" ");
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}
