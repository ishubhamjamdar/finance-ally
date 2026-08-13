import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TradeBar } from "@/components/TradeBar";
import { ApiError } from "@/lib/api";
import type { TradeFill, TradeOrder } from "@/lib/types";

function makeFill(overrides: Partial<TradeFill> = {}): TradeFill {
  return {
    id: "trade-1",
    ticker: "AAPL",
    side: "buy",
    quantity: 5,
    price: 190.5,
    value: 952.5,
    executed_at: "2026-08-12T09:30:00+00:00",
    ...overrides,
  };
}

/** The prop under test, named so a `vi.fn` can carry its signature. */
type Submit = (order: TradeOrder) => Promise<TradeFill>;

function type(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

describe("TradeBar", () => {
  it("sends the ticker, side and quantity — and never a price", () => {
    // PLAN.md §8: the fill price comes from the server's cache, and a request
    // naming its own is rejected rather than silently ignored.
    const onSubmit = vi.fn<Submit>(async () => makeFill());
    render(<TradeBar onSubmit={onSubmit} />);

    type("Ticker", "aapl");
    type("Quantity", "5");
    fireEvent.click(screen.getByRole("button", { name: "Buy" }));

    expect(onSubmit).toHaveBeenCalledWith({ ticker: "AAPL", side: "buy", quantity: 5 });
    expect(Object.keys(onSubmit.mock.calls[0][0])).not.toContain("price");
  });

  it("sells through the same path", () => {
    const onSubmit = vi.fn<Submit>(async () => makeFill({ side: "sell" }));
    render(<TradeBar onSubmit={onSubmit} />);

    type("Ticker", "MSFT");
    type("Quantity", "2");
    fireEvent.click(screen.getByRole("button", { name: "Sell" }));

    expect(onSubmit).toHaveBeenCalledWith({ ticker: "MSFT", side: "sell", quantity: 2 });
  });

  it("accepts a fractional quantity", () => {
    const onSubmit = vi.fn<Submit>(async () => makeFill({ quantity: 0.5 }));
    render(<TradeBar onSubmit={onSubmit} />);

    type("Ticker", "AAPL");
    type("Quantity", "0.5");
    fireEvent.click(screen.getByRole("button", { name: "Buy" }));

    expect(onSubmit).toHaveBeenCalledWith({ ticker: "AAPL", side: "buy", quantity: 0.5 });
  });

  it("confirms the fill at the price the server used", async () => {
    render(<TradeBar onSubmit={async () => makeFill()} />);

    type("Ticker", "AAPL");
    type("Quantity", "5");
    fireEvent.click(screen.getByRole("button", { name: "Buy" }));

    await waitFor(() =>
      expect(screen.getByTestId("trade-status")).toHaveTextContent(
        "Bought 5 AAPL at 190.50 — $952.50",
      ),
    );
  });

  it("shows a rejected trade instead of failing silently", async () => {
    // The exit criterion. A trade that vanishes is the user believing they own
    // something they do not.
    render(
      <TradeBar
        onSubmit={async () => {
          throw new ApiError("Insufficient cash: need $19,050.00, have $10,000.00", 400);
        }}
      />,
    );

    type("Ticker", "AAPL");
    type("Quantity", "100");
    fireEvent.click(screen.getByRole("button", { name: "Buy" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Insufficient cash: need $19,050.00, have $10,000.00");
  });

  it("reports an unreachable server in words rather than 'Failed to fetch'", async () => {
    render(
      <TradeBar
        onSubmit={async () => {
          throw new TypeError("Failed to fetch");
        }}
      />,
    );

    type("Ticker", "AAPL");
    type("Quantity", "1");
    fireEvent.click(screen.getByRole("button", { name: "Buy" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot reach the server");
  });

  it("does not spend a round trip on an empty form", () => {
    const onSubmit = vi.fn<Submit>(async () => makeFill());
    render(<TradeBar onSubmit={onSubmit} />);

    fireEvent.click(screen.getByRole("button", { name: "Buy" }));

    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("Enter a ticker.");
  });

  it("refuses a quantity of zero or less before sending it", () => {
    const onSubmit = vi.fn<Submit>(async () => makeFill());
    render(<TradeBar onSubmit={onSubmit} selected="AAPL" />);

    type("Quantity", "0");
    fireEvent.click(screen.getByRole("button", { name: "Buy" }));
    expect(onSubmit).not.toHaveBeenCalled();

    type("Quantity", "-3");
    fireEvent.click(screen.getByRole("button", { name: "Buy" }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("greater than zero");
  });

  it("fills the ticker from the chart until the user types over it", () => {
    const onSubmit = vi.fn<Submit>(async () => makeFill());
    const { rerender } = render(<TradeBar onSubmit={onSubmit} selected="AAPL" />);
    expect(screen.getByLabelText("Ticker")).toHaveValue("AAPL");

    // Following the chart…
    rerender(<TradeBar onSubmit={onSubmit} selected="MSFT" />);
    expect(screen.getByLabelText("Ticker")).toHaveValue("MSFT");

    // …until it is typed in, after which the field is the user's.
    type("Ticker", "NVDA");
    rerender(<TradeBar onSubmit={onSubmit} selected="TSLA" />);
    expect(screen.getByLabelText("Ticker")).toHaveValue("NVDA");
  });

  it("keeps a deliberately cleared field cleared", () => {
    const onSubmit = vi.fn<Submit>(async () => makeFill());
    render(<TradeBar onSubmit={onSubmit} selected="AAPL" />);

    type("Ticker", "");

    expect(screen.getByLabelText("Ticker")).toHaveValue("");
  });

  it("clears the quantity after a fill but never the symbol", async () => {
    // The trap this avoids: the user types NVDA, sells it, then types a new
    // quantity and clicks Sell again. If the field had reverted to the charted
    // MSFT, that second order would sell a different holding — with no
    // confirmation dialog anywhere in this path to catch it.
    const onSubmit = vi.fn<Submit>(async () => makeFill({ ticker: "NVDA", side: "sell" }));
    render(<TradeBar onSubmit={onSubmit} selected="MSFT" />);

    type("Ticker", "NVDA");
    type("Quantity", "1");
    fireEvent.click(screen.getByRole("button", { name: "Sell" }));

    await waitFor(() => expect(screen.getByLabelText("Quantity")).toHaveValue(null));
    expect(screen.getByLabelText("Ticker")).toHaveValue("NVDA");

    type("Quantity", "2");
    fireEvent.click(screen.getByRole("button", { name: "Sell" }));
    expect(onSubmit).toHaveBeenLastCalledWith({ ticker: "NVDA", side: "sell", quantity: 2 });
  });

  it("keeps following the chart when the field was never typed in", async () => {
    const onSubmit = vi.fn<Submit>(async () => makeFill({ ticker: "MSFT" }));
    const { rerender } = render(<TradeBar onSubmit={onSubmit} selected="MSFT" />);

    type("Quantity", "1");
    fireEvent.click(screen.getByRole("button", { name: "Buy" }));
    await waitFor(() => expect(screen.getByLabelText("Quantity")).toHaveValue(null));

    rerender(<TradeBar onSubmit={onSubmit} selected="TSLA" />);
    expect(screen.getByLabelText("Ticker")).toHaveValue("TSLA");
  });

  it("refuses a symbol the server would reject, without a round trip", async () => {
    // FastAPI answers a bad symbol with a field-level 422; catching the shape
    // here turns that into a sentence the person typing it can act on.
    const onSubmit = vi.fn<Submit>(async () => makeFill());
    render(<TradeBar onSubmit={onSubmit} />);

    type("Ticker", "SPY500!!");
    type("Quantity", "1");
    fireEvent.click(screen.getByRole("button", { name: "Buy" }));

    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("is not a ticker symbol");
  });

  it("keeps the order on screen when it was rejected, so it can be corrected", async () => {
    render(
      <TradeBar
        onSubmit={async () => {
          throw new ApiError("Not enough shares", 400);
        }}
        selected="MSFT"
      />,
    );

    type("Ticker", "AAPL");
    type("Quantity", "100");
    fireEvent.click(screen.getByRole("button", { name: "Buy" }));

    await screen.findByRole("alert");
    expect(screen.getByLabelText("Ticker")).toHaveValue("AAPL");
    expect(screen.getByLabelText("Quantity")).toHaveValue(100);
  });

  it("disables both buttons while an order is in flight", async () => {
    let release: ((fill: TradeFill) => void) | null = null;
    const onSubmit = vi.fn<Submit>(
      () =>
        new Promise<TradeFill>((resolve) => {
          release = resolve;
        }),
    );
    render(<TradeBar onSubmit={onSubmit} selected="AAPL" />);

    type("Quantity", "1");
    fireEvent.click(screen.getByRole("button", { name: "Buy" }));
    expect(screen.getByTestId("trade-status")).toHaveTextContent("Sending order…");

    fireEvent.click(screen.getByRole("button", { name: "Sell" }));
    expect(onSubmit).toHaveBeenCalledTimes(1);

    release!(makeFill());
    await waitFor(() => expect(screen.getByTestId("trade-status")).toHaveTextContent("Bought"));
  });

  it("refuses a second order even when the buttons are bypassed", async () => {
    // Mutation testing found the test above proves nothing about the `pending`
    // guard: the *disabled attribute* was stopping the second click, so
    // deleting the guard changed nothing and no test noticed.
    //
    // Submitting the form directly is the path the attribute does not cover —
    // and it is a real one, since a form with inputs submits on Enter. Without
    // the guard this sends a duplicate order against live money.
    let release: ((fill: TradeFill) => void) | null = null;
    const onSubmit = vi.fn<Submit>(
      () =>
        new Promise<TradeFill>((resolve) => {
          release = resolve;
        }),
    );
    const { container } = render(<TradeBar onSubmit={onSubmit} selected="AAPL" />);
    const form = container.querySelector("form");
    if (form === null) throw new Error("the trade bar has no form");

    type("Quantity", "1");
    fireEvent.submit(form);
    expect(onSubmit).toHaveBeenCalledTimes(1);

    fireEvent.submit(form);
    fireEvent.submit(form);
    expect(onSubmit).toHaveBeenCalledTimes(1);

    release!(makeFill());
    await waitFor(() => expect(screen.getByTestId("trade-status")).toHaveTextContent("Bought"));
  });

  it("clears the last error when the next order succeeds", async () => {
    const onSubmit = vi
      .fn<Submit>()
      .mockRejectedValueOnce(new ApiError("Insufficient cash", 400))
      .mockResolvedValueOnce(makeFill());
    render(<TradeBar onSubmit={onSubmit} selected="AAPL" />);

    type("Quantity", "100");
    fireEvent.click(screen.getByRole("button", { name: "Buy" }));
    await screen.findByRole("alert");

    type("Quantity", "1");
    fireEvent.click(screen.getByRole("button", { name: "Buy" }));

    await waitFor(() => expect(screen.getByTestId("trade-status")).toHaveTextContent("Bought"));
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
