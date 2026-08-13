import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PortfolioHeatmap } from "@/components/PortfolioHeatmap";
import { markPositions } from "@/lib/valuation";
import { makePortfolio, makePosition, makeQuote } from "@/test/fixtures";

function rows(positions: ReturnType<typeof makePosition>[], prices = {}) {
  return markPositions(makePortfolio(0, positions), prices);
}

/** A tile's area as a fraction of the box, from the percentages it was given. */
function area(ticker: string): number {
  const tile = screen.getByTestId(`tile-${ticker}`);
  const width = Number.parseFloat(tile.style.width);
  const height = Number.parseFloat(tile.style.height);
  return (width / 100) * (height / 100);
}

describe("PortfolioHeatmap", () => {
  it("sizes each tile by portfolio weight", () => {
    render(
      <PortfolioHeatmap
        positions={rows(
          [makePosition("AAPL", 30, 100, 100), makePosition("MSFT", 10, 100, 100)],
          { AAPL: makeQuote("AAPL", 100), MSFT: makeQuote("MSFT", 100) },
        )}
      />,
    );

    expect(area("AAPL")).toBeCloseTo(0.75, 2);
    expect(area("MSFT")).toBeCloseTo(0.25, 2);
    expect(area("AAPL") + area("MSFT")).toBeCloseTo(1, 2);
  });

  it("re-sizes when a price moves the weights", () => {
    const positions = [makePosition("AAPL", 10, 100, 100), makePosition("MSFT", 10, 100, 100)];
    const { rerender } = render(<PortfolioHeatmap positions={rows(positions)} />);
    expect(area("AAPL")).toBeCloseTo(0.5, 2);

    rerender(
      <PortfolioHeatmap
        positions={rows(positions, {
          AAPL: makeQuote("AAPL", 300),
          MSFT: makeQuote("MSFT", 100),
        })}
      />,
    );

    expect(area("AAPL")).toBeCloseTo(0.75, 2);
  });

  it("colours by the sign of P&L", () => {
    render(
      <PortfolioHeatmap
        positions={rows([makePosition("WIN", 1, 100, 100), makePosition("LOSE", 1, 100, 100)], {
          WIN: makeQuote("WIN", 120),
          LOSE: makeQuote("LOSE", 80),
        })}
      />,
    );

    expect(screen.getByTestId("tile-WIN").style.backgroundColor).toContain("--color-up");
    expect(screen.getByTestId("tile-LOSE").style.backgroundColor).toContain("--color-down");
  });

  it("carries the magnitude in the intensity, clamped at the top", () => {
    render(
      <PortfolioHeatmap
        positions={rows(
          [
            makePosition("MILD", 1, 100, 100),
            makePosition("STRONG", 1, 100, 100),
            makePosition("EXTREME", 1, 100, 100),
          ],
          {
            MILD: makeQuote("MILD", 101),
            STRONG: makeQuote("STRONG", 108),
            EXTREME: makeQuote("EXTREME", 200),
          },
        )}
      />,
    );

    const mix = (ticker: string) =>
      Number.parseFloat(
        /--color-\w+\)\s+(\d+)%/.exec(screen.getByTestId(`tile-${ticker}`).style.backgroundColor)?.[1] ??
          "0",
      );

    expect(mix("MILD")).toBeLessThan(mix("STRONG"));
    // Past the saturation point the colour stops carrying information rather
    // than running away to white.
    expect(mix("EXTREME")).toBe(mix("STRONG"));
  });

  it("survives an empty portfolio", () => {
    render(<PortfolioHeatmap positions={[]} />);

    expect(screen.getByTestId("heatmap-empty")).toHaveTextContent(/No positions yet/);
    expect(screen.queryByTestId("heatmap")).toBeNull();
  });

  it("names an unpriced holding under the map instead of tiling it", () => {
    // It cannot be sized — there is no market value to size it by — and a tile
    // drawn from its cost would be a rectangle of one unit beside rectangles
    // of another.
    render(
      <PortfolioHeatmap
        positions={rows([makePosition("AAPL", 1, 100, 100), makePosition("GHOST", 1, 100, null)], {
          AAPL: makeQuote("AAPL", 100),
        })}
      />,
    );

    expect(screen.queryByTestId("tile-GHOST")).toBeNull();
    expect(screen.getByTestId("heatmap-unpriced")).toHaveTextContent("GHOST");
    expect(area("AAPL")).toBeCloseTo(1, 2);
  });

  it("says why the map is empty when nothing at all can be priced", () => {
    render(<PortfolioHeatmap positions={rows([makePosition("GHOST", 1, 100, null)])} />);

    expect(screen.getByTestId("heatmap-empty")).toHaveTextContent(/no held ticker has a price/);
    expect(screen.getByTestId("heatmap-unpriced")).toHaveTextContent("GHOST");
  });

  it("charts the ticker whose tile is clicked", () => {
    const onSelect = vi.fn();
    render(
      <PortfolioHeatmap
        positions={rows([makePosition("AAPL", 1, 100, 100)], { AAPL: makeQuote("AAPL", 110) })}
        onSelect={onSelect}
      />,
    );

    fireEvent.click(screen.getByTestId("tile-AAPL"));

    expect(onSelect).toHaveBeenCalledWith("AAPL");
  });

  it("names each tile's weight and P&L, so the colour is not the only channel", () => {
    render(
      <PortfolioHeatmap
        positions={rows([makePosition("AAPL", 1, 100, 100)], { AAPL: makeQuote("AAPL", 110) })}
      />,
    );

    expect(
      screen.getByRole("button", { name: /AAPL, \+100\.00% of positions, \+10\.00%/ }),
    ).toBeInTheDocument();
  });

  it("marks the charted tile", () => {
    render(
      <PortfolioHeatmap
        positions={rows([makePosition("AAPL", 1, 100, 100), makePosition("MSFT", 1, 100, 100)], {
          AAPL: makeQuote("AAPL", 100),
          MSFT: makeQuote("MSFT", 100),
        })}
        selected="MSFT"
      />,
    );

    expect(screen.getByTestId("tile-MSFT")).toHaveAttribute("data-selected", "true");
    expect(screen.getByTestId("tile-AAPL")).not.toHaveAttribute("data-selected");
  });
});
