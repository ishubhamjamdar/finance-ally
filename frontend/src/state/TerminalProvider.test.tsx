import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TerminalProvider, useAccount, useMarket } from "@/state/TerminalProvider";
import { FakeEventSource } from "@/test/FakeEventSource";
import { makePortfolio } from "@/test/fixtures";

/** Enough of `fetch` for a `vi.fn` to type its recorded calls. */
type Fetch = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

/** The three reads the provider makes on mount, routed by path. */
function stubFetch() {
  const fetchMock = vi.fn<Fetch>(async (input) => {
    const url = String(input);
    const body = url.includes("watchlist")
      ? { tickers: [{ ticker: "AAPL", added_at: "2026-08-12T09:30:00Z", quote: null }] }
      : url.includes("history")
        ? { snapshots: [{ total_value: 10000, recorded_at: "2026-08-12T09:30:00+00:00" }] }
        : url.includes("/trade")
          ? {
              trade: {
                id: "t1",
                ticker: "AAPL",
                side: "buy",
                quantity: 2,
                price: 190.5,
                value: 381,
                executed_at: "2026-08-12T09:31:00+00:00",
              },
              portfolio: makePortfolio(9619),
            }
          : makePortfolio(10000);
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** How many requests one mount makes: portfolio, watchlist, history. */
const READS = 3;

/** Two independent consumers, as Checkpoint 6's panels will be. */
function TwoConsumers() {
  return (
    <>
      <Consumer id="a" />
      <Consumer id="b" />
    </>
  );
}

function Consumer({ id }: { id: string }) {
  const market = useMarket();
  const account = useAccount();
  return (
    <div data-testid={`consumer-${id}`}>
      {market.status}:{account.watchlist.length}:{account.portfolio?.cash_balance ?? "none"}
    </div>
  );
}

beforeEach(() => {
  FakeEventSource.install();
  stubFetch();
});

afterEach(() => {
  vi.unstubAllGlobals();
  FakeEventSource.reset();
});

describe("TerminalProvider", () => {
  it("opens one price stream however many components consume it", () => {
    render(
      <TerminalProvider>
        <TwoConsumers />
      </TerminalProvider>,
    );

    // The whole reason the provider exists: `usePriceStream` opens a
    // connection per call, so two panels calling it directly would be two
    // streams for one page.
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it("fetches the account once, not once per consumer", async () => {
    const fetchMock = stubFetch();

    render(
      <TerminalProvider>
        <TwoConsumers />
      </TerminalProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("consumer-a")).toHaveTextContent("10000"));
    expect(fetchMock).toHaveBeenCalledTimes(READS); // portfolio, watchlist, history — once each
  });

  it("keeps each resource's failure to itself", async () => {
    // A merged error field puts the portfolio's failure over the watchlist's
    // perfectly current rows — and on a first load, over the whole panel.
    const fetchMock = vi.fn<Fetch>(async (input) => {
      const url = String(input);
      if (url.includes("watchlist")) {
        return new Response(JSON.stringify({ tickers: [] }), { status: 200 });
      }
      return new Response(JSON.stringify({ detail: "No market data source is running" }), {
        status: 503,
        headers: { "content-type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    function Errors() {
      const account = useAccount();
      return (
        <div data-testid="errors">
          {account.portfolioError ?? "-"}|{account.watchlistError ?? "-"}|
          {account.historyError ?? "-"}
        </div>
      );
    }

    render(
      <TerminalProvider>
        <Errors />
      </TerminalProvider>,
    );

    await waitFor(() =>
      expect(screen.getByTestId("errors")).toHaveTextContent(
        "No market data source is running|-|No market data source is running",
      ),
    );
  });

  it("gives every consumer the same state", async () => {
    render(
      <TerminalProvider>
        <TwoConsumers />
      </TerminalProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("consumer-a")).toHaveTextContent("10000"));
    expect(screen.getByTestId("consumer-b").textContent).toBe(
      screen.getByTestId("consumer-a").textContent,
    );
  });

  it("closes the stream when the provider unmounts", () => {
    const { unmount } = render(
      <TerminalProvider>
        <Consumer id="a" />
      </TerminalProvider>,
    );
    const source = FakeEventSource.only;

    unmount();

    expect(source.closeCalls).toBe(1);
    expect(source.listenerCount()).toBe(0);
  });

  it("refuses to be used outside the provider rather than serving empty data", () => {
    // A panel rendered outside the provider would otherwise show $0.00 and an
    // empty watchlist, which looks like a wiped account rather than a bug.
    const noise = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      expect(() => render(<Consumer id="a" />)).toThrow(/TerminalProvider/);
    } finally {
      noise.mockRestore();
    }
  });

  it("re-reads the account on refresh, and nothing else", async () => {
    const fetchMock = stubFetch();

    // Shaped like Checkpoint 6's trade bar: something the user does, which
    // then has to make every panel agree again.
    function Trader() {
      const { refresh } = useAccount();
      return <button onClick={refresh}>Buy</button>;
    }

    render(
      <TerminalProvider>
        <Trader />
      </TerminalProvider>,
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(READS));

    fireEvent.click(screen.getByRole("button", { name: "Buy" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(READS * 2));
    // A refresh must not disturb the price stream — reopening it would drop
    // every sparkline the page has accumulated.
    expect(FakeEventSource.instances).toHaveLength(1);
  });
  describe("mutations", () => {
    /** A consumer shaped like the trade bar and the watchlist controls. */
    function Actor() {
      const { trade, addTicker, removeTicker } = useAccount();
      return (
        <>
          <button onClick={() => void trade({ ticker: "AAPL", side: "buy", quantity: 2 })}>
            Trade
          </button>
          <button onClick={() => void addTicker("PYPL")}>Add</button>
          <button onClick={() => void removeTicker("AAPL")}>Remove</button>
        </>
      );
    }

    function requestsTo(fetchMock: ReturnType<typeof stubFetch>, needle: string) {
      return fetchMock.mock.calls.filter(([input]) => String(input).includes(needle));
    }

    it("posts a trade and then re-reads every panel's data", async () => {
      const fetchMock = stubFetch();
      render(
        <TerminalProvider>
          <Actor />
        </TerminalProvider>,
      );
      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(READS));

      fireEvent.click(screen.getByRole("button", { name: "Trade" }));

      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(READS * 2 + 1));
      const [url, init] = requestsTo(fetchMock, "/api/portfolio/trade")[0];
      expect(String(url)).toBe("/api/portfolio/trade");
      if (init === undefined) throw new Error("the trade was sent with no request init");
      expect(init.method).toBe("POST");
      expect(JSON.parse(init.body as string)).toEqual({
        ticker: "AAPL",
        side: "buy",
        quantity: 2,
      });
    });

    it("returns the fill to the caller, so the trade bar can confirm it", async () => {
      stubFetch();
      let fill: unknown = null;

      function Trader() {
        const { trade } = useAccount();
        return (
          <button
            onClick={() => {
              void trade({ ticker: "AAPL", side: "buy", quantity: 2 }).then((result) => {
                fill = result;
              });
            }}
          >
            Trade
          </button>
        );
      }

      render(
        <TerminalProvider>
          <Trader />
        </TerminalProvider>,
      );
      fireEvent.click(screen.getByRole("button", { name: "Trade" }));

      await waitFor(() => expect(fill).toMatchObject({ ticker: "AAPL", price: 190.5 }));
    });

    it("does not refresh when the trade was refused", async () => {
      // Nothing changed, so there is nothing to re-read — and a refresh here
      // would suggest to the user that something happened.
      const fetchMock = vi.fn<Fetch>(async (input) => {
        if (String(input).includes("/trade")) {
          return new Response(JSON.stringify({ detail: "Insufficient cash" }), {
            status: 400,
            headers: { "content-type": "application/json" },
          });
        }
        return new Response(JSON.stringify(makePortfolio(10000)), { status: 200 });
      });
      vi.stubGlobal("fetch", fetchMock);

      let reason: string | null = null;
      function Trader() {
        const { trade } = useAccount();
        return (
          <button
            onClick={() => {
              void trade({ ticker: "AAPL", side: "buy", quantity: 2 }).catch((cause: Error) => {
                reason = cause.message;
              });
            }}
          >
            Trade
          </button>
        );
      }

      render(
        <TerminalProvider>
          <Trader />
        </TerminalProvider>,
      );
      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(READS));

      fireEvent.click(screen.getByRole("button", { name: "Trade" }));

      await waitFor(() => expect(reason).toBe("Insufficient cash"));
      expect(fetchMock).toHaveBeenCalledTimes(READS + 1);
    });

    it("adds a ticker and re-reads", async () => {
      const fetchMock = stubFetch();
      render(
        <TerminalProvider>
          <Actor />
        </TerminalProvider>,
      );
      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(READS));

      fireEvent.click(screen.getByRole("button", { name: "Add" }));

      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(READS * 2 + 1));
      const posts = fetchMock.mock.calls.filter(
        ([url, init]) => String(url) === "/api/watchlist" && init?.method === "POST",
      );
      expect(posts).toHaveLength(1);
      expect(JSON.parse(posts[0][1]?.body as string)).toEqual({ ticker: "PYPL" });
    });

    it("removes a ticker and re-reads the portfolio too", async () => {
      // Removing a ticker held as a position leaves the position in place —
      // the panels have to keep showing it, so the portfolio is re-read as
      // well as the list.
      const fetchMock = stubFetch();
      render(
        <TerminalProvider>
          <Actor />
        </TerminalProvider>,
      );
      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(READS));

      fireEvent.click(screen.getByRole("button", { name: "Remove" }));

      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(READS * 2 + 1));
      const deletes = fetchMock.mock.calls.filter(([, init]) => init?.method === "DELETE");
      expect(deletes).toHaveLength(1);
      expect(String(deletes[0][0])).toBe("/api/watchlist/AAPL");
      expect(requestsTo(fetchMock, "/api/portfolio").length).toBeGreaterThanOrEqual(2);
    });

    it("leaves the price stream alone through every mutation", async () => {
      // Reopening it would discard every sparkline the page has accumulated.
      const fetchMock = stubFetch();
      render(
        <TerminalProvider>
          <Actor />
        </TerminalProvider>,
      );
      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(READS));

      fireEvent.click(screen.getByRole("button", { name: "Trade" }));
      fireEvent.click(screen.getByRole("button", { name: "Add" }));
      fireEvent.click(screen.getByRole("button", { name: "Remove" }));

      await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(READS + 3));
      expect(FakeEventSource.instances).toHaveLength(1);
    });
  });
});
