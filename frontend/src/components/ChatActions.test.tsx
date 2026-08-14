import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ChatActions } from "@/components/ChatActions";
import type { ChatAction } from "@/lib/types";

function filled(ticker = "MSFT"): ChatAction {
  return {
    kind: "trade",
    ok: true,
    summary: `buy 3 ${ticker}`,
    detail: `Filled 3 ${ticker} at $419.91.`,
    ticker,
    action: "buy",
    result: { id: "t1", ticker, side: "buy", quantity: 3, price: 419.91, value: 1259.73 },
  };
}

function refused(ticker = "AAPL"): ChatAction {
  return {
    kind: "trade",
    ok: false,
    summary: `buy 100000 ${ticker}`,
    detail: `Insufficient cash: ${ticker} x100000 at $190.03 costs $19,003,000.00, but only $8,740.27 is available.`,
    ticker,
    action: "buy",
    result: null,
  };
}

function watched(action: "add" | "remove" = "add"): ChatAction {
  return {
    kind: "watchlist",
    ok: true,
    summary: `${action} PYPL`,
    detail: "PYPL added to the watchlist.",
    ticker: "PYPL",
    action,
    result: { ticker: "PYPL", added_at: "2026-08-14T03:22:17+00:00" },
  };
}

describe("ChatActions", () => {
  it("renders an executed trade with the backend's own wording", () => {
    render(<ChatActions actions={[filled()]} />);

    expect(screen.getByTestId("chat-action-ok")).toHaveTextContent("buy 3 MSFT");
    expect(screen.getByTestId("chat-action-ok")).toHaveTextContent("Filled 3 MSFT at $419.91.");
  });

  it("renders a refused trade, with the reason, rather than dropping it", () => {
    // The whole point of this component. The model writes its message before
    // it knows whether anything cleared, so a reply can say "Buying 100000
    // AAPL" beside an action that was refused — and a panel that showed only
    // the message would be a transcript that lies.
    render(<ChatActions actions={[refused()]} />);

    const failure = screen.getByTestId("chat-action-failed");
    expect(failure).toHaveTextContent("buy 100000 AAPL");
    expect(failure).toHaveTextContent("Insufficient cash");
    expect(screen.queryByTestId("chat-action-ok")).toBeNull();
  });

  it("renders every action in a mixed reply, not just the ones that worked", () => {
    render(<ChatActions actions={[filled("MSFT"), refused("AAPL"), watched()]} />);

    expect(screen.getAllByTestId("chat-action-ok")).toHaveLength(2);
    expect(screen.getAllByTestId("chat-action-failed")).toHaveLength(1);
  });

  it("says so when a reply only partly executed", () => {
    // Three trades of which one failed is the case most easily misread as
    // success.
    render(<ChatActions actions={[filled("MSFT"), filled("NVDA"), refused("AAPL")]} />);

    expect(screen.getByTestId("chat-actions")).toHaveTextContent("2 of 3 executed");
  });

  it("does not count a reply where everything worked", () => {
    render(<ChatActions actions={[filled("MSFT"), filled("NVDA")]} />);

    expect(screen.getByTestId("chat-actions")).not.toHaveTextContent("of 2 executed");
  });

  it("does not count a reply where nothing worked — every line already says so", () => {
    render(<ChatActions actions={[refused("AAPL"), refused("TSLA")]} />);

    expect(screen.getByTestId("chat-actions")).not.toHaveTextContent("of 2 executed");
    expect(screen.getAllByTestId("chat-action-failed")).toHaveLength(2);
  });

  it("names the outcome in words, not only in colour", () => {
    render(<ChatActions actions={[filled(), refused()]} />);

    expect(screen.getByText("Executed:")).toBeInTheDocument();
    expect(screen.getByText("Refused:")).toBeInTheDocument();
  });

  it("labels a watchlist change as one", () => {
    render(<ChatActions actions={[watched()]} />);

    expect(screen.getByTestId("chat-action-ok")).toHaveTextContent("watchlist");
    expect(screen.getByTestId("chat-action-ok")).toHaveTextContent("PYPL added to the watchlist.");
  });

  it("draws two identical actions twice", () => {
    // A reply may hold two identical items, and both moved money.
    render(<ChatActions actions={[filled("MSFT"), filled("MSFT")]} />);

    expect(screen.getAllByTestId("chat-action-ok")).toHaveLength(2);
  });

  it("renders nothing at all when a reply executed nothing", () => {
    render(<ChatActions actions={[]} />);

    expect(screen.queryByTestId("chat-actions")).toBeNull();
  });
});
