import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FeedPanel } from "@/components/FeedPanel";
import { EM_DASH } from "@/lib/format";

const SHOCK = {
  ticker: "TSLA",
  magnitude_percent: -3.4,
  price: 238.12,
  timestamp: 1_760_000_000,
};

describe("FeedPanel", () => {
  it("shows dashes, not zeros, before the first frame", () => {
    render(
      <FeedPanel status="connecting" frames={0} lastFrameAt={null} tickerCount={0} shocks={[]} />,
    );

    const metrics = screen.getAllByRole("definition").map((node) => node.textContent);
    expect(metrics).toEqual([EM_DASH, EM_DASH, EM_DASH]);
  });

  it("reports frames received and tickers priced once streaming", () => {
    render(
      <FeedPanel
        status="connected"
        frames={42}
        lastFrameAt={1_760_000_000_000}
        tickerCount={10}
        shocks={[]}
      />,
    );

    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("10")).toBeInTheDocument();
    expect(screen.getByTestId("feed-detail")).toHaveTextContent("Streaming");
  });

  it("says the prices on screen are stale when the feed is down", () => {
    render(
      <FeedPanel
        status="disconnected"
        frames={42}
        lastFrameAt={1_760_000_000_000}
        tickerCount={10}
        shocks={[]}
      />,
    );

    expect(screen.getByTestId("feed-detail")).toHaveTextContent(/last received/i);
  });

  it("lists a shock with its signed magnitude", () => {
    render(
      <FeedPanel
        status="connected"
        frames={1}
        lastFrameAt={1_760_000_000_000}
        tickerCount={1}
        shocks={[SHOCK]}
      />,
    );

    const entry = screen.getByRole("listitem");
    expect(entry).toHaveTextContent("TSLA");
    expect(entry).toHaveTextContent("-3.40%");
    expect(entry).toHaveTextContent("238.12");
  });

  it("has an honest empty state rather than looking broken", () => {
    render(
      <FeedPanel status="connected" frames={5} lastFrameAt={1} tickerCount={10} shocks={[]} />,
    );

    expect(screen.getByText(/nothing yet/i)).toBeInTheDocument();
    expect(screen.queryByRole("listitem")).not.toBeInTheDocument();
  });
});
