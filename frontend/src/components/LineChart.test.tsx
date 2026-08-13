import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LineChart } from "@/components/LineChart";

/** The `points` attribute of the drawn series, as numbers. */
function drawnPoints(): { x: number; y: number }[] {
  const polyline = document.querySelector("polyline");
  if (polyline === null) throw new Error("no series was drawn");
  return (polyline.getAttribute("points") ?? "")
    .split(" ")
    .filter((pair) => pair !== "")
    .map((pair) => {
      const [x, y] = pair.split(",").map(Number);
      return { x, y };
    });
}

describe("LineChart", () => {
  it("says what it has instead of drawing an empty plot", () => {
    render(<LineChart values={[]} label="Price" empty="Waiting for prices." testId="c" />);

    expect(screen.getByTestId("c")).toHaveTextContent("Waiting for prices.");
    expect(document.querySelector("polyline")).toBeNull();
  });

  it("draws one point per value, oldest at the left", () => {
    render(<LineChart values={[10, 20, 30]} label="Price" empty="none" testId="c" />);

    const points = drawnPoints();
    expect(points).toHaveLength(3);
    expect(points[0].x).toBe(0);
    expect(points[2].x).toBe(100);
    // Rising series: the last point is nearer the top, which is a *smaller* y.
    expect(points[2].y).toBeLessThan(points[0].y);
  });

  it("scales to the series' own range, not to zero", () => {
    // A portfolio moving between 10,000 and 10,050 is a flat line if the axis
    // starts at zero. The whole point of the panel is the shape of that move.
    render(<LineChart values={[10000, 10050]} label="Value" empty="none" testId="c" />);

    const [first, last] = drawnPoints();
    expect(last.y).toBeLessThan(first.y - 50);
  });

  it("draws a flat series down the middle instead of losing it", () => {
    // The failure this guards: a zero range divides by zero, every point comes
    // out NaN, and the line vanishes — which looks exactly like a broken feed.
    render(<LineChart values={[100, 100, 100]} label="Price" empty="none" testId="c" />);

    for (const point of drawnPoints()) {
      expect(Number.isFinite(point.y)).toBe(true);
      expect(point.y).toBeCloseTo(50, 6);
    }
  });

  it("draws a single point down the middle of the box", () => {
    render(<LineChart values={[42]} label="Price" empty="none" testId="c" />);

    const points = drawnPoints();
    expect(points).toHaveLength(1);
    expect(points[0].x).toBeCloseTo(50, 6);
  });

  it("drops a non-finite value rather than drawing it as zero", () => {
    render(<LineChart values={[10, Number.NaN, 30]} label="Price" empty="none" testId="c" />);

    const points = drawnPoints();
    expect(points).toHaveLength(2);
    for (const point of points) expect(Number.isFinite(point.y)).toBe(true);
  });

  it("labels the axis in the series' own units", () => {
    render(
      <LineChart
        values={[100, 200]}
        label="Price"
        empty="none"
        format={(value) => `$${value.toFixed(0)}`}
        testId="c"
      />,
    );

    // Five gridlines across a padded domain: the top label is above the high
    // and the bottom one below the low.
    const labels = screen.getByTestId("c").textContent ?? "";
    expect(labels).toContain("$");
    expect(labels).toMatch(/\$2\d\d/);
  });

  it("captions what the x axis spans", () => {
    render(
      <LineChart values={[1, 2]} label="Price" empty="none" from="09:30:00" to="09:31:00" testId="c" />,
    );

    expect(screen.getByTestId("c")).toHaveTextContent("09:30:00");
    expect(screen.getByTestId("c")).toHaveTextContent("09:31:00");
  });

  it("marks the live end of the series", () => {
    render(<LineChart values={[10, 20]} label="Price" empty="none" testId="c" />);

    expect(screen.getByTestId("c-marker")).toBeInTheDocument();
  });

  it("names the series and its latest value for a screen reader", () => {
    render(
      <LineChart
        values={[10, 20, 31.5]}
        label="AAPL price"
        empty="none"
        format={(value) => value.toFixed(2)}
        testId="c"
      />,
    );

    expect(screen.getByRole("img", { name: /AAPL price: 3 points, 31\.50/ })).toBeInTheDocument();
  });

  it("gives the plot both of its dimensions, so it is not sized by its viewBox", () => {
    // The defect this is a stand-in for, found by driving a real browser at
    // Gate 3: an `svg` is a replaced element with an intrinsic aspect ratio
    // taken from its `viewBox`. With only a height, the browser computed a
    // *square* — the series drew across 45% of a wide panel, and the live-end
    // marker, positioned in CSS rather than in the viewBox, sat stranded to
    // the right of where the line stopped.
    //
    // jsdom performs no layout, so it cannot measure that. What it can check
    // is the thing that caused it: whether the plot declares both dimensions.
    // The real guard is a browser assertion on the rendered width, and that
    // belongs to Checkpoint 9.
    render(<LineChart values={[1, 2, 3]} label="Price" empty="none" testId="c" />);

    const svg = document.querySelector("svg");
    expect(svg).toHaveClass("h-full");
    expect(svg).toHaveClass("w-full");
  });
});
