import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Sparkline } from "@/components/Sparkline";

function pointsOf(container: HTMLElement): [number, number][] {
  const attribute = container.querySelector("polyline")?.getAttribute("points") ?? "";
  return attribute
    .split(" ")
    .filter(Boolean)
    .map((pair) => pair.split(",").map(Number) as [number, number]);
}

describe("Sparkline", () => {
  it("draws a placeholder rule, not a line, with nothing accumulated", () => {
    const { container } = render(<Sparkline points={[]} />);

    expect(container.querySelector("polyline")).toBeNull();
    expect(container.querySelector("line")).not.toBeNull();
  });

  it("draws one vertex per point", () => {
    const { container } = render(<Sparkline points={[1, 2, 3, 4]} />);

    expect(pointsOf(container)).toHaveLength(4);
  });

  it("spans the full width and puts the highest point at the top", () => {
    const { container } = render(<Sparkline points={[10, 30, 20]} width={100} height={20} />);
    const points = pointsOf(container);

    expect(points[0][0]).toBe(0);
    expect(points[2][0]).toBe(100);

    const [lowest, highest] = [points[0][1], points[1][1]];
    expect(highest).toBeLessThan(lowest); // SVG y grows downward
  });

  it("draws a flat series down the middle rather than dividing by zero", () => {
    const { container } = render(<Sparkline points={[50, 50, 50]} height={20} />);

    for (const [, y] of pointsOf(container)) {
      expect(Number.isFinite(y)).toBe(true);
      expect(y).toBeCloseTo(10, 1);
    }
  });

  it("draws a single point as a visible line", () => {
    const { container } = render(<Sparkline points={[42]} width={80} />);
    const points = pointsOf(container);

    expect(points).toHaveLength(2);
    expect(points[0][0]).toBe(0);
    expect(points[1][0]).toBe(80);
  });

  it("ignores non-finite points instead of rendering NaN coordinates", () => {
    const { container } = render(<Sparkline points={[10, Number.NaN, 20]} />);
    const points = pointsOf(container);

    expect(points).toHaveLength(2);
    for (const [x, y] of points) {
      expect(Number.isFinite(x)).toBe(true);
      expect(Number.isFinite(y)).toBe(true);
    }
  });

  it("is green when the series ends higher and red when it ends lower", () => {
    const { container: rising } = render(<Sparkline points={[10, 20]} />);
    expect(rising.querySelector("svg")?.getAttribute("class")).toContain("text-up");

    const { container: falling } = render(<Sparkline points={[20, 10]} />);
    expect(falling.querySelector("svg")?.getAttribute("class")).toContain("text-down");
  });
});
