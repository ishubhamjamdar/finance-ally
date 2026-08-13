/**
 * The workstation assembled — Checkpoint 6's exit criteria, at the level they
 * are stated: click a row and the chart follows, buy and every panel agrees
 * again, a rejected trade is visible.
 *
 * These are deliberately not component tests. Each panel is covered on its own
 * beside its file; what only this level can catch is the wiring between them —
 * a selection that reaches the chart but not the trade bar, or a fill that
 * updates the header and leaves the positions table stale.
 */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Page from "@/app/page";
import { FakeEventSource } from "@/test/FakeEventSource";
import { makeFrame, makePortfolio, makePosition } from "@/test/fixtures";

/** The account the fake backend is holding, mutated by a trade like the real one. */
let account: ReturnType<typeof makePortfolio>;
let history: { total_value: number; recorded_at: string }[];
let tradeStatus: { status: number; detail: string } | null;
let watchlistTickers: string[];
let portfolioStatus: number;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => {
  FakeEventSource.install();
  account = makePortfolio(10000);
  history = [{ total_value: 10000, recorded_at: "2026-08-12T09:30:00+00:00" }];
  tradeStatus = null;
  watchlistTickers = ["AAPL", "MSFT"];
  portfolioStatus = 200;

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);

      if (url.includes("/api/portfolio/trade")) {
        if (tradeStatus !== null) return json({ detail: tradeStatus.detail }, tradeStatus.status);
        const order = JSON.parse(init?.body as string) as { ticker: string; quantity: number };
        // What the backend does: fill at the cached price, update cash and the
        // position, and write a snapshot immediately.
        account = makePortfolio(10000 - order.quantity * 190, [
          makePosition(order.ticker, order.quantity, 190, 190),
        ]);
        history = [...history, { total_value: 10000, recorded_at: "2026-08-12T09:31:00+00:00" }];
        return json({
          trade: {
            id: "t1",
            ticker: order.ticker,
            side: "buy",
            quantity: order.quantity,
            price: 190,
            value: order.quantity * 190,
            executed_at: "2026-08-12T09:31:00+00:00",
          },
          portfolio: account,
        });
      }

      if (url.includes("/api/portfolio/history")) return json({ snapshots: history });
      if (url.includes("/api/portfolio")) {
        return portfolioStatus === 200
          ? json(account)
          : json({ detail: "No market data source is running" }, portfolioStatus);
      }
      if (url.includes("/api/watchlist")) {
        return json({
          tickers: watchlistTickers.map((ticker, index) => ({
            ticker,
            added_at: `2026-08-12T09:30:0${index}+00:00`,
            quote: null,
          })),
        });
      }
      throw new Error(`unexpected request: ${url}`);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  FakeEventSource.reset();
});

/** Wait for the first watchlist fetch to land, so the grid exists. */
async function loaded() {
  await screen.findByTestId("row-AAPL");
}

describe("the workstation", () => {
  it("charts the first watched ticker without waiting to be asked", async () => {
    render(<Page />);
    await loaded();

    expect(screen.getByTestId("chart-ticker")).toHaveTextContent("AAPL");
  });

  it("charts the ticker whose watchlist row is clicked", async () => {
    render(<Page />);
    await loaded();

    fireEvent.click(screen.getByTestId("row-MSFT"));

    expect(screen.getByTestId("chart-ticker")).toHaveTextContent("MSFT");
    expect(screen.getByTestId("row-MSFT")).toHaveAttribute("data-selected", "true");
  });

  it("draws the selected ticker's own series, not another's", async () => {
    render(<Page />);
    await loaded();

    FakeEventSource.only.emitMessage(makeFrame({ AAPL: 190, MSFT: 400 }));
    FakeEventSource.only.emitMessage(makeFrame({ AAPL: 191, MSFT: 402 }));
    await waitFor(() => expect(screen.getByTestId("chart-price")).toHaveTextContent("191.00"));

    fireEvent.click(screen.getByTestId("row-MSFT"));

    expect(screen.getByTestId("chart-price")).toHaveTextContent("402.00");
    expect(screen.getByTestId("chart-low")).toHaveTextContent("400.00");
  });

  it("hands the charted ticker to the trade bar", async () => {
    render(<Page />);
    await loaded();

    fireEvent.click(screen.getByTestId("row-MSFT"));

    expect(screen.getByLabelText("Ticker")).toHaveValue("MSFT");
  });

  it("updates cash, the positions table, the heatmap and the header on a buy", async () => {
    // The checkpoint's central exit criterion, and the reason `refresh()`
    // lives in the provider rather than in each panel.
    render(<Page />);
    await loaded();
    FakeEventSource.only.emitMessage(makeFrame({ AAPL: 190, MSFT: 400 }));

    expect(screen.getByTestId("header-cash")).toHaveTextContent("$10,000.00");
    expect(screen.getByText(/No positions\. Buy something/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Quantity"), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "Buy" }));

    await waitFor(() => expect(screen.getByTestId("header-cash")).toHaveTextContent("$8,100.00"));
    expect(screen.getByTestId("position-AAPL")).toHaveTextContent("AAPL");
    expect(screen.getByTestId("tile-AAPL")).toBeInTheDocument();
    // 8,100 cash + 10 × 190 streamed.
    expect(screen.getByTestId("header-total")).toHaveTextContent("$10,000");
    expect(screen.getByTestId("trade-status")).toHaveTextContent("Bought 10 AAPL at 190.00");
  });

  it("extends the P&L chart with the snapshot the trade wrote", async () => {
    render(<Page />);
    await loaded();
    expect(screen.getByTestId("pnl-count")).toHaveTextContent("1 snapshots");

    fireEvent.change(screen.getByLabelText("Quantity"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "Buy" }));

    await waitFor(() => expect(screen.getByTestId("pnl-count")).toHaveTextContent("2 snapshots"));
  });

  it("shows a rejected trade instead of letting it vanish", async () => {
    tradeStatus = { status: 400, detail: "Insufficient cash: need $19,000.00, have $10,000.00" };
    render(<Page />);
    await loaded();

    fireEvent.change(screen.getByLabelText("Quantity"), { target: { value: "100" } });
    fireEvent.click(screen.getByRole("button", { name: "Buy" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Insufficient cash");
    // And nothing moved.
    expect(screen.getByTestId("header-cash")).toHaveTextContent("$10,000.00");
    expect(screen.getByText(/No positions\. Buy something/)).toBeInTheDocument();
  });

  it("keeps marking the positions table from the stream between trades", async () => {
    render(<Page />);
    await loaded();

    fireEvent.change(screen.getByLabelText("Quantity"), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "Buy" }));
    await screen.findByTestId("position-AAPL");

    FakeEventSource.only.emitMessage(makeFrame({ AAPL: 200 }));

    await waitFor(() =>
      expect(screen.getByTestId("position-price-AAPL")).toHaveTextContent("200.00"),
    );
    // 10 × 200 against a cost of 1,900.
    expect(screen.getByTestId("position-pnl-AAPL")).toHaveTextContent("+$100.00");
  });

  it("charts a position selected from the table or the heatmap", async () => {
    render(<Page />);
    await loaded();
    fireEvent.click(screen.getByTestId("row-MSFT"));

    fireEvent.change(screen.getByLabelText("Quantity"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "Buy" }));
    await screen.findByTestId("position-MSFT");

    fireEvent.click(screen.getByTestId("row-AAPL"));
    expect(screen.getByTestId("chart-ticker")).toHaveTextContent("AAPL");

    fireEvent.click(screen.getByTestId("tile-MSFT"));
    expect(screen.getByTestId("chart-ticker")).toHaveTextContent("MSFT");
  });

  it("opens exactly one price stream for the whole page", async () => {
    // Six panels now read prices. Every one of them goes through the provider;
    // a panel calling `usePriceStream` itself would be a second copy of every
    // frame, and nothing would look wrong until the count was high.
    render(<Page />);
    await loaded();

    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it("adds a ticker to the watchlist and starts charting it when picked", async () => {
    render(<Page />);
    await loaded();

    fireEvent.change(screen.getByLabelText("Add ticker"), { target: { value: "pypl" } });
    fireEvent.click(screen.getByRole("button", { name: "Add to watchlist" }));

    await waitFor(() => {
      const posts = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.filter(
        ([url, init]) =>
          String(url) === "/api/watchlist" && (init as RequestInit | undefined)?.method === "POST",
      );
      expect(posts).toHaveLength(1);
      expect(JSON.parse((posts[0][1] as RequestInit).body as string)).toEqual({ ticker: "PYPL" });
    });
  });

  it("removes a ticker from the watchlist", async () => {
    render(<Page />);
    await loaded();

    fireEvent.click(screen.getByRole("button", { name: "Remove MSFT from the watchlist" }));

    await waitFor(() => {
      const deletes = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.filter(
        ([, init]) => (init as RequestInit | undefined)?.method === "DELETE",
      );
      expect(deletes).toHaveLength(1);
      expect(String(deletes[0][0])).toBe("/api/watchlist/MSFT");
    });
  });

  it("falls back to another ticker when the charted one leaves the watchlist", async () => {
    render(<Page />);
    await loaded();

    // MSFT has been priced, and `usePriceStream` never forgets a quote. If the
    // fallback consulted `prices` the chart would stay pinned to MSFT here,
    // showing a frozen price with no row on screen to explain it — and a test
    // that emitted no frames would pass against that bug.
    FakeEventSource.only.emitMessage(makeFrame({ AAPL: 190, MSFT: 400 }));
    fireEvent.click(screen.getByTestId("row-MSFT"));
    await waitFor(() => expect(screen.getByTestId("chart-price")).toHaveTextContent("400.00"));

    watchlistTickers = ["AAPL"];
    fireEvent.click(screen.getByRole("button", { name: "Remove MSFT from the watchlist" }));

    await waitFor(() => expect(screen.queryByTestId("row-MSFT")).toBeNull());
    expect(screen.getByTestId("chart-ticker")).toHaveTextContent("AAPL");
  });

  it("keeps charting a removed ticker that is still held", async () => {
    // The backend keeps streaming a ticker it still has a position in, so the
    // chart has real data to draw and going flat the instant you stop watching
    // it would be the wrong answer.
    render(<Page />);
    await loaded();
    FakeEventSource.only.emitMessage(makeFrame({ AAPL: 190, MSFT: 400 }));

    fireEvent.click(screen.getByTestId("row-MSFT"));
    fireEvent.change(screen.getByLabelText("Quantity"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "Buy" }));
    await screen.findByTestId("position-MSFT");

    watchlistTickers = ["AAPL"];
    fireEvent.click(screen.getByRole("button", { name: "Remove MSFT from the watchlist" }));

    await waitFor(() => expect(screen.queryByTestId("row-MSFT")).toBeNull());
    expect(screen.getByTestId("chart-ticker")).toHaveTextContent("MSFT");
  });

  it("counts the tickers the last frame carried, not every one ever seen", async () => {
    // `prices` is append-only on purpose, so counting its keys would report
    // thirteen priced beside a grid of ten after three removals — against the
    // one distinction the feed panel exists to make.
    render(<Page />);
    await loaded();

    FakeEventSource.only.emitMessage(makeFrame({ AAPL: 190, MSFT: 400 }));
    await waitFor(() => expect(screen.getByText("Tickers priced").parentElement).toHaveTextContent("2"));

    FakeEventSource.only.emitMessage(makeFrame({ AAPL: 191 }));

    await waitFor(() =>
      expect(screen.getByText("Tickers priced").parentElement).toHaveTextContent("1"),
    );
    // …and the row that stopped being priced keeps its last value.
    expect(screen.getByTestId("price-MSFT")).toHaveTextContent("400.00");
  });

  it("keeps a failed portfolio read out of the watchlist panel", async () => {
    // Two resources, two errors. A portfolio that will not load must not paint
    // "cannot reach the server" over a grid of live, streaming prices.
    portfolioStatus = 503;
    render(<Page />);
    await loaded();
    FakeEventSource.only.emitMessage(makeFrame({ AAPL: 190 }));

    await waitFor(() => expect(screen.getByTestId("price-AAPL")).toHaveTextContent("190.00"));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getAllByTestId(/^row-/)).toHaveLength(2);
  });

  it("names an unpriced holding rather than showing it as worthless", async () => {
    account = makePortfolio(5000, [makePosition("AAPL", 10, 200, null)]);
    render(<Page />);
    await loaded();

    await waitFor(() => expect(screen.getByTestId("header-unpriced")).toHaveTextContent("AAPL"));
    expect(screen.getByTestId("heatmap-unpriced")).toHaveTextContent("AAPL");
    // The row survives with its cost intact.
    expect(within(screen.getByTestId("position-AAPL")).getByText("200.00")).toBeInTheDocument();
  });
});
