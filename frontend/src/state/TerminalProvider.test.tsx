import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TerminalProvider, useAccount, useMarket } from "@/state/TerminalProvider";
import { FakeEventSource } from "@/test/FakeEventSource";
import { makePortfolio } from "@/test/fixtures";

function stubFetch() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const body = url.includes("watchlist")
      ? { tickers: [{ ticker: "AAPL", added_at: "2026-08-12T09:30:00Z", quote: null }] }
      : makePortfolio(10000);
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

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
    expect(fetchMock).toHaveBeenCalledTimes(2); // portfolio and watchlist, once each
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
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    fireEvent.click(screen.getByRole("button", { name: "Buy" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
    // A refresh must not disturb the price stream — reopening it would drop
    // every sparkline the page has accumulated.
    expect(FakeEventSource.instances).toHaveLength(1);
  });
});
