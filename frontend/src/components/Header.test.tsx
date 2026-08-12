import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Header } from "@/components/Header";
import { EM_DASH } from "@/lib/format";
import { makePortfolio, makePosition, makeQuote } from "@/test/fixtures";

describe("Header", () => {
  it("shows cash and the total before any position exists", () => {
    render(<Header portfolio={makePortfolio(10000)} prices={{}} status="connected" />);

    expect(screen.getByTestId("header-cash")).toHaveTextContent("$10,000.00");
    expect(screen.getByTestId("header-total")).toHaveTextContent("$10,000");
  });

  it("marks positions against the stream, not the fetched price", () => {
    const portfolio = makePortfolio(5000, [makePosition("AAPL", 10, 180, 190)]);

    render(
      <Header
        portfolio={portfolio}
        prices={{ AAPL: makeQuote("AAPL", 200) }}
        status="connected"
      />,
    );

    // 5000 cash + 10 x 200 streamed, not 10 x 190 fetched.
    expect(screen.getByTestId("header-total")).toHaveTextContent("$7,000");
  });

  it("falls back to the fetched mark for a ticker the stream has not sent", () => {
    const portfolio = makePortfolio(5000, [makePosition("AAPL", 10, 180, 190)]);

    render(<Header portfolio={portfolio} prices={{}} status="connected" />);

    expect(screen.getByTestId("header-total")).toHaveTextContent("$6,900");
  });

  it("names an unpriced holding instead of counting it as zero", () => {
    const portfolio = makePortfolio(5000, [makePosition("AAPL", 10, 180, null)]);

    render(<Header portfolio={portfolio} prices={{}} status="connected" />);

    expect(screen.getByTestId("header-unpriced")).toHaveTextContent("AAPL");
    expect(screen.getByTestId("header-total")).toHaveTextContent("$5,000");
  });

  it("renders dashes rather than zeros before the portfolio loads", () => {
    render(<Header portfolio={null} prices={{}} status="connecting" />);

    expect(screen.getByTestId("header-total")).toHaveTextContent(EM_DASH);
    expect(screen.getByTestId("header-cash")).toHaveTextContent(EM_DASH);
  });

  it("reports the feed status by colour and by name", () => {
    const { rerender } = render(<Header portfolio={null} prices={{}} status="connected" />);
    expect(screen.getByRole("status")).toHaveAccessibleName("Market feed: Live");
    expect(screen.getByTestId("connection-dot").className).toContain("bg-up");

    rerender(<Header portfolio={null} prices={{}} status="reconnecting" />);
    expect(screen.getByRole("status")).toHaveAccessibleName("Market feed: Reconnecting");
    expect(screen.getByTestId("connection-dot").className).toContain("bg-accent");

    rerender(<Header portfolio={null} prices={{}} status="disconnected" />);
    expect(screen.getByRole("status")).toHaveAccessibleName("Market feed: Disconnected");
    expect(screen.getByTestId("connection-dot").className).toContain("bg-down");
  });
});
