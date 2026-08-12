import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WatchlistPanel } from "@/components/WatchlistPanel";
import { FLASH_MS } from "@/hooks/usePriceFlash";
import { EM_DASH } from "@/lib/format";
import { makeQuote, makeWatchlistRow } from "@/test/fixtures";

const ROWS = [makeWatchlistRow("AAPL"), makeWatchlistRow("MSFT"), makeWatchlistRow("TSLA")];

describe("WatchlistPanel", () => {
  it("renders a row per watched ticker, in the order given", () => {
    render(<WatchlistPanel rows={ROWS} prices={{}} sparklines={{}} />);

    const symbols = screen.getAllByRole("rowheader").map((cell) => cell.textContent);
    expect(symbols).toEqual(["AAPL", "MSFT", "TSLA"]);
  });

  it("renders live prices and the daily change", () => {
    render(
      <WatchlistPanel
        rows={ROWS}
        prices={{
          AAPL: makeQuote("AAPL", 190.5, { previous_close: 188 }),
        }}
        sparklines={{ AAPL: [188, 190.5] }}
      />,
    );

    expect(screen.getByTestId("price-AAPL")).toHaveTextContent("190.50");
    expect(screen.getByTestId("day-AAPL")).toHaveTextContent("+1.33%");
  });

  it("renders an em dash, never a zero, for a ticker with no price yet", () => {
    render(<WatchlistPanel rows={ROWS} prices={{}} sparklines={{}} />);

    expect(screen.getByTestId("price-MSFT")).toHaveTextContent(EM_DASH);
    expect(screen.getByTestId("day-MSFT")).toHaveTextContent(EM_DASH);
  });

  it("falls back to the quote the watchlist endpoint returned", () => {
    render(
      <WatchlistPanel
        rows={[makeWatchlistRow("NFLX", makeQuote("NFLX", 612.4))]}
        prices={{}}
        sparklines={{}}
      />,
    );

    expect(screen.getByTestId("price-NFLX")).toHaveTextContent("612.40");
  });

  it("shows the loading and error states instead of an empty grid", () => {
    const { rerender } = render(
      <WatchlistPanel rows={[]} prices={{}} sparklines={{}} loading={true} />,
    );
    expect(screen.getByText(/loading watchlist/i)).toBeInTheDocument();

    rerender(
      <WatchlistPanel rows={[]} prices={{}} sparklines={{}} error="Cannot reach the server" />,
    );
    expect(screen.getByText("Cannot reach the server")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("renders a sparkline whose length follows the accumulated series", () => {
    const { rerender } = render(<WatchlistPanel rows={ROWS} prices={{}} sparklines={{}} />);

    // Nothing accumulated yet: a placeholder rule, not a fabricated line.
    expect(document.querySelector('[data-testid="row-AAPL"] polyline')).toBeNull();

    rerender(<WatchlistPanel rows={ROWS} prices={{}} sparklines={{ AAPL: [100, 101, 99] }} />);

    const polyline = document.querySelector('[data-testid="row-AAPL"] polyline');
    expect(polyline?.getAttribute("points")?.split(" ")).toHaveLength(3);
  });

  describe("price flash", () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it("applies flash-up when the price rises and flash-down when it falls", () => {
      const { rerender } = render(
        <WatchlistPanel
          rows={ROWS}
          prices={{ AAPL: makeQuote("AAPL", 190) }}
          sparklines={{}}
        />,
      );

      expect(screen.getByTestId("price-AAPL").className).not.toMatch(/flash/);

      rerender(
        <WatchlistPanel
          rows={ROWS}
          prices={{ AAPL: makeQuote("AAPL", 191, { previous_price: 190 }) }}
          sparklines={{}}
        />,
      );
      expect(screen.getByTestId("price-AAPL")).toHaveClass("flash-up");

      act(() => {
        vi.advanceTimersByTime(FLASH_MS);
      });

      rerender(
        <WatchlistPanel
          rows={ROWS}
          prices={{ AAPL: makeQuote("AAPL", 189, { previous_price: 191 }) }}
          sparklines={{}}
        />,
      );
      expect(screen.getByTestId("price-AAPL")).toHaveClass("flash-down");
    });

    it("fades the flash rather than leaving it stuck on the cell", () => {
      const { rerender } = render(
        <WatchlistPanel rows={ROWS} prices={{ AAPL: makeQuote("AAPL", 190) }} sparklines={{}} />,
      );

      rerender(
        <WatchlistPanel
          rows={ROWS}
          prices={{ AAPL: makeQuote("AAPL", 191, { previous_price: 190 }) }}
          sparklines={{}}
        />,
      );
      expect(screen.getByTestId("price-AAPL")).toHaveClass("flash-up");

      act(() => {
        vi.advanceTimersByTime(FLASH_MS);
      });

      expect(screen.getByTestId("price-AAPL").className).not.toMatch(/flash/);
    });

    it("flashes only the row whose price moved", () => {
      const prices = { AAPL: makeQuote("AAPL", 190), MSFT: makeQuote("MSFT", 420) };
      const { rerender } = render(
        <WatchlistPanel rows={ROWS} prices={prices} sparklines={{}} />,
      );

      rerender(
        <WatchlistPanel
          rows={ROWS}
          prices={{ ...prices, AAPL: makeQuote("AAPL", 191, { previous_price: 190 }) }}
          sparklines={{}}
        />,
      );

      expect(screen.getByTestId("price-AAPL")).toHaveClass("flash-up");
      expect(screen.getByTestId("price-MSFT").className).not.toMatch(/flash/);
    });
  });
});
