import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PnlChart, formatStamp } from "@/components/PnlChart";
import type { Snapshot } from "@/lib/types";

function snapshot(totalValue: number, minute: number): Snapshot {
  return {
    total_value: totalValue,
    recorded_at: `2026-08-12T09:${String(minute).padStart(2, "0")}:00+00:00`,
  };
}

describe("PnlChart", () => {
  it("draws the snapshot series", () => {
    render(<PnlChart snapshots={[snapshot(10000, 30), snapshot(10250, 31)]} />);

    expect(document.querySelector("polyline")).not.toBeNull();
    expect(screen.getByTestId("pnl-count")).toHaveTextContent("2 snapshots");
  });

  it("reports the change across the series in money and percent", () => {
    render(<PnlChart snapshots={[snapshot(10000, 30), snapshot(10250, 31)]} />);

    expect(screen.getByTestId("pnl-change")).toHaveTextContent("+$250.00");
    expect(screen.getByTestId("pnl-change")).toHaveTextContent("+2.50%");
    expect(screen.getByTestId("pnl-change").className).toContain("text-up");
  });

  it("goes red on a drawdown", () => {
    render(<PnlChart snapshots={[snapshot(10000, 30), snapshot(9500, 31)]} />);

    expect(screen.getByTestId("pnl-change")).toHaveTextContent("-$500.00");
    expect(screen.getByTestId("pnl-change").className).toContain("text-down");
  });

  it("extends when a new snapshot arrives", () => {
    const { rerender } = render(<PnlChart snapshots={[snapshot(10000, 30)]} />);
    const before = document.querySelector("polyline")?.getAttribute("points");

    rerender(<PnlChart snapshots={[snapshot(10000, 30), snapshot(10400, 31)]} />);

    expect(screen.getByTestId("pnl-count")).toHaveTextContent("2 snapshots");
    expect(document.querySelector("polyline")?.getAttribute("points")).not.toBe(before);
  });

  it("says when the first snapshot is due rather than drawing a flat fake line", () => {
    render(<PnlChart snapshots={[]} />);

    expect(screen.getByTestId("pnl-chart")).toHaveTextContent(/No snapshots yet/);
    expect(document.querySelector("polyline")).toBeNull();
  });

  it("says it is loading before the first fetch lands", () => {
    render(<PnlChart snapshots={[]} loading />);

    expect(screen.getByTestId("pnl-chart")).toHaveTextContent(/Loading the value series/);
  });

  it("shows the error when there is no series to fall back on", () => {
    render(<PnlChart snapshots={[]} error="Cannot reach the server" />);

    expect(screen.getByTestId("pnl-chart")).toHaveTextContent("Cannot reach the server");
  });

  it("keeps the drawn series through a failed poll and says so above it", () => {
    // The poll runs every 30 seconds unattended. Blanking a chart the user was
    // reading because one of them failed loses more than it explains.
    render(
      <PnlChart
        snapshots={[snapshot(10000, 30), snapshot(10250, 31)]}
        error="Cannot reach the server"
      />,
    );

    expect(document.querySelector("polyline")).not.toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent("Cannot reach the server");
  });

  it("labels the axis with the span the series actually covers", () => {
    render(<PnlChart snapshots={[snapshot(10000, 30), snapshot(10250, 45)]} />);

    const chart = screen.getByTestId("pnl-chart");
    expect(chart.textContent).toContain(formatStamp("2026-08-12T09:30:00+00:00"));
    expect(chart.textContent).toContain(formatStamp("2026-08-12T09:45:00+00:00"));
  });
});

describe("formatStamp", () => {
  it("reads the offset the backend writes", () => {
    // `utc_now()` is `datetime.now(timezone.utc).isoformat()`, so `+00:00`.
    // Two stamps an hour apart must render an hour apart in any timezone.
    const [hour] = formatStamp("2026-08-12T09:30:00+00:00").split(":").map(Number);
    const [later] = formatStamp("2026-08-12T10:30:00+00:00").split(":").map(Number);

    expect(later).toBe((hour + 1) % 24);
  });

  it("treats a zoneless stamp as UTC rather than as local time", () => {
    // Without the guard, `Date` reads a bare ISO string as local time and the
    // whole axis shifts by the viewer's offset.
    expect(formatStamp("2026-08-12T09:30:00")).toBe(formatStamp("2026-08-12T09:30:00Z"));
  });

  it("shows an unparseable stamp as it came rather than 'Invalid Date'", () => {
    expect(formatStamp("not a timestamp")).toBe("not a timestamp");
  });
});
