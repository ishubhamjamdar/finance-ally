import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PositionsTable } from "@/components/PositionsTable";
import { EM_DASH } from "@/lib/format";
import { markPositions } from "@/lib/valuation";
import { makePortfolio, makePosition, makeQuote } from "@/test/fixtures";

/** The live rows, marked exactly as `page.tsx` marks them. */
function rows(positions: ReturnType<typeof makePosition>[], prices = {}) {
  return markPositions(makePortfolio(0, positions), prices);
}

describe("PositionsTable", () => {
  it("shows every column PLAN.md §10 asks for", () => {
    render(
      <PositionsTable
        positions={rows([makePosition("AAPL", 10, 180, 190)], {
          AAPL: makeQuote("AAPL", 200),
        })}
      />,
    );

    const row = screen.getByTestId("position-AAPL");
    expect(row).toHaveTextContent("AAPL");
    expect(row).toHaveTextContent("10"); // quantity
    expect(row).toHaveTextContent("180.00"); // average cost
    expect(row).toHaveTextContent("200.00"); // live price, not the fetched 190
    expect(row).toHaveTextContent("$2,000.00"); // market value
    expect(row).toHaveTextContent("+$200.00"); // unrealised P&L
    expect(row).toHaveTextContent("+11.11%");
  });

  it("marks a loss red and a gain green", () => {
    render(
      <PositionsTable
        positions={rows([makePosition("AAPL", 10, 200, 200), makePosition("MSFT", 1, 100, 100)], {
          AAPL: makeQuote("AAPL", 180),
          MSFT: makeQuote("MSFT", 120),
        })}
      />,
    );

    expect(screen.getByTestId("position-pnl-AAPL").className).toContain("text-down");
    expect(screen.getByTestId("position-pnl-AAPL")).toHaveTextContent("-$200.00");
    expect(screen.getByTestId("position-pnl-MSFT").className).toContain("text-up");
  });

  it("keeps an unpriced holding on screen with dashes rather than zeros", () => {
    // A position missing from the table is indistinguishable from one that was
    // sold, and a zero P&L is a claim the data does not support.
    render(<PositionsTable positions={rows([makePosition("AAPL", 4, 200, null)])} />);

    const row = screen.getByTestId("position-AAPL");
    expect(row).toHaveTextContent("AAPL");
    expect(row).toHaveTextContent("4");
    expect(row).toHaveTextContent("200.00"); // the cost is still known
    expect(screen.getByTestId("position-price-AAPL")).toHaveTextContent(EM_DASH);
    expect(screen.getByTestId("position-pnl-AAPL")).toHaveTextContent(EM_DASH);
  });

  it("renders a fractional quantity without inventing precision", () => {
    render(<PositionsTable positions={rows([makePosition("AAPL", 0.5, 200, 200)])} />);

    expect(screen.getByTestId("position-AAPL")).toHaveTextContent("0.5");
  });

  it("invites a first trade instead of showing an empty grid", () => {
    render(<PositionsTable positions={[]} />);

    expect(screen.getByText(/No positions/)).toBeInTheDocument();
  });

  it("says it is loading before the first fetch lands", () => {
    render(<PositionsTable positions={[]} loading />);

    expect(screen.getByText(/Loading positions/)).toBeInTheDocument();
  });

  it("charts the ticker whose row is clicked", () => {
    const onSelect = vi.fn();
    render(
      <PositionsTable positions={rows([makePosition("AAPL", 1, 180, 190)])} onSelect={onSelect} />,
    );

    fireEvent.click(screen.getByTestId("position-AAPL"));

    expect(onSelect).toHaveBeenCalledWith("AAPL");
  });

  it("offers the same selection to the keyboard", () => {
    const onSelect = vi.fn();
    render(
      <PositionsTable positions={rows([makePosition("AAPL", 1, 180, 190)])} onSelect={onSelect} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "AAPL" }));

    // Once, not twice: the button click must not also fire the row's handler.
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it("marks the charted row", () => {
    render(
      <PositionsTable
        positions={rows([makePosition("AAPL", 1, 180, 190), makePosition("MSFT", 1, 400, 400)])}
        selected="MSFT"
      />,
    );

    expect(screen.getByTestId("position-MSFT")).toHaveAttribute("data-selected", "true");
    expect(screen.getByTestId("position-AAPL")).not.toHaveAttribute("data-selected");
  });

  it("flashes a row's price when it moves, and not when it first arrives", () => {
    const positions = [makePosition("AAPL", 1, 180, null)];
    const { rerender } = render(<PositionsTable positions={rows(positions)} />);
    expect(screen.getByTestId("position-price-AAPL").className).not.toContain("flash");

    rerender(<PositionsTable positions={rows(positions, { AAPL: makeQuote("AAPL", 190) })} />);
    expect(screen.getByTestId("position-price-AAPL").className).not.toContain("flash");

    rerender(<PositionsTable positions={rows(positions, { AAPL: makeQuote("AAPL", 191) })} />);
    expect(screen.getByTestId("position-price-AAPL").className).toContain("flash-up");
  });
});
