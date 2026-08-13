import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WatchlistPanel } from "@/components/WatchlistPanel";
import { FLASH_MS } from "@/hooks/usePriceFlash";
import { ApiError } from "@/lib/api";
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

  it("keeps the rows when a reload fails, and says so above them", () => {
    // `useApiResource` holds the last good data through a failed request for
    // exactly this reason, and those rows are still being marked by the
    // stream. Replacing them with one line of red text loses more than it
    // explains — and Checkpoint 6's trade bar makes reloads routine.
    render(
      <WatchlistPanel
        rows={ROWS}
        prices={{ AAPL: makeQuote("AAPL", 190.5) }}
        sparklines={{}}
        error="Cannot reach the server"
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("Cannot reach the server");
    expect(screen.getAllByRole("rowheader")).toHaveLength(3);
    expect(screen.getByTestId("price-AAPL")).toHaveTextContent("190.50");
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

  describe("selection", () => {
    it("charts the ticker whose row is clicked", () => {
      const onSelect = vi.fn();
      render(<WatchlistPanel rows={ROWS} prices={{}} sparklines={{}} onSelect={onSelect} />);

      fireEvent.click(screen.getByTestId("row-MSFT"));

      expect(onSelect).toHaveBeenCalledWith("MSFT");
    });

    it("offers the same selection to the keyboard, once", () => {
      const onSelect = vi.fn();
      render(<WatchlistPanel rows={ROWS} prices={{}} sparklines={{}} onSelect={onSelect} />);

      fireEvent.click(screen.getByRole("button", { name: "MSFT" }));

      // The row handler must not fire as well, or a keyboard user selects and
      // instantly re-selects.
      expect(onSelect).toHaveBeenCalledTimes(1);
      expect(onSelect).toHaveBeenCalledWith("MSFT");
    });

    it("marks the charted row", () => {
      render(<WatchlistPanel rows={ROWS} prices={{}} sparklines={{}} selected="TSLA" />);

      expect(screen.getByTestId("row-TSLA")).toHaveAttribute("data-selected", "true");
      expect(screen.getByTestId("row-AAPL")).not.toHaveAttribute("data-selected");
    });
  });

  describe("add and remove", () => {
    it("adds the ticker that was typed, normalised", async () => {
      const onAdd = vi.fn(async () => {});
      render(<WatchlistPanel rows={ROWS} prices={{}} sparklines={{}} onAdd={onAdd} />);

      fireEvent.change(screen.getByLabelText("Add ticker"), { target: { value: "pypl" } });
      fireEvent.click(screen.getByRole("button", { name: "Add to watchlist" }));

      await waitFor(() => expect(onAdd).toHaveBeenCalledWith("PYPL"));
    });

    it("clears the field on success so the next add starts empty", async () => {
      render(
        <WatchlistPanel rows={ROWS} prices={{}} sparklines={{}} onAdd={async () => {}} />,
      );

      fireEvent.change(screen.getByLabelText("Add ticker"), { target: { value: "PYPL" } });
      fireEvent.click(screen.getByRole("button", { name: "Add to watchlist" }));

      await waitFor(() => expect(screen.getByLabelText("Add ticker")).toHaveValue(""));
    });

    it("shows why an add was refused, and keeps the symbol for correcting", async () => {
      // 409 duplicate, 400 list full, 503 no feed — every one of them arrives
      // with the backend's own wording, and the user can act on all three.
      render(
        <WatchlistPanel
          rows={ROWS}
          prices={{}}
          sparklines={{}}
          onAdd={async () => {
            throw new ApiError("AAPL is already on the watchlist", 409);
          }}
        />,
      );

      fireEvent.change(screen.getByLabelText("Add ticker"), { target: { value: "AAPL" } });
      fireEvent.click(screen.getByRole("button", { name: "Add to watchlist" }));

      expect(await screen.findByTestId("watchlist-error")).toHaveTextContent(
        "AAPL is already on the watchlist",
      );
      expect(screen.getByLabelText("Add ticker")).toHaveValue("AAPL");
    });

    it("does not spend a round trip on an empty field", () => {
      const onAdd = vi.fn(async () => {});
      render(<WatchlistPanel rows={ROWS} prices={{}} sparklines={{}} onAdd={onAdd} />);

      fireEvent.click(screen.getByRole("button", { name: "Add to watchlist" }));

      expect(onAdd).not.toHaveBeenCalled();
      expect(screen.getByTestId("watchlist-error")).toHaveTextContent("Enter a ticker to add.");
    });

    it("removes the ticker whose button is clicked, without selecting it", async () => {
      const onRemove = vi.fn(async () => {});
      const onSelect = vi.fn();
      render(
        <WatchlistPanel
          rows={ROWS}
          prices={{}}
          sparklines={{}}
          onRemove={onRemove}
          onSelect={onSelect}
        />,
      );

      fireEvent.click(
        screen.getByRole("button", { name: "Remove MSFT from the watchlist" }),
      );

      await waitFor(() => expect(onRemove).toHaveBeenCalledWith("MSFT"));
      expect(onSelect).not.toHaveBeenCalled();
    });

    it("shows why a removal was refused", async () => {
      render(
        <WatchlistPanel
          rows={ROWS}
          prices={{}}
          sparklines={{}}
          onRemove={async () => {
            throw new ApiError("No market data source is running", 503);
          }}
        />,
      );

      fireEvent.click(screen.getByRole("button", { name: "Remove AAPL from the watchlist" }));

      expect(await screen.findByTestId("watchlist-error")).toHaveTextContent(
        "No market data source is running",
      );
    });

    it("offers no controls at all when the handlers are not supplied", () => {
      render(<WatchlistPanel rows={ROWS} prices={{}} sparklines={{}} />);

      expect(screen.queryByLabelText("Add ticker")).toBeNull();
      expect(screen.queryByRole("button", { name: /Remove/ })).toBeNull();
    });
  });
});
