import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PriceChart } from "@/components/PriceChart";
import { EM_DASH } from "@/lib/format";
import { makeQuote } from "@/test/fixtures";

describe("PriceChart", () => {
  it("charts the selected ticker with its live price and day change", () => {
    render(
      <PriceChart
        ticker="AAPL"
        quote={makeQuote("AAPL", 195.5, { previous_close: 190 })}
        points={[190, 192, 195.5]}
      />,
    );

    expect(screen.getByTestId("chart-ticker")).toHaveTextContent("AAPL");
    expect(screen.getByTestId("chart-price")).toHaveTextContent("195.50");
    expect(screen.getByTestId("chart-day")).toHaveTextContent("+2.89%");
    expect(document.querySelector("polyline")).not.toBeNull();
  });

  it("reports the window's low and high, which a line alone does not give", () => {
    render(
      <PriceChart ticker="AAPL" quote={makeQuote("AAPL", 195)} points={[188.25, 201.5, 195]} />,
    );

    expect(screen.getByTestId("chart-low")).toHaveTextContent("188.25");
    expect(screen.getByTestId("chart-high")).toHaveTextContent("201.50");
  });

  it("asks the user to pick a ticker rather than drawing nothing", () => {
    render(<PriceChart ticker={null} quote={null} points={[]} />);

    expect(screen.getByTestId("chart-ticker")).toHaveTextContent(EM_DASH);
    expect(screen.getByTestId("price-chart")).toHaveTextContent(/Select a ticker/);
  });

  it("says the chart fills in from the stream when a ticker has no points yet", () => {
    // There is no historical price endpoint in this system, and nothing is
    // back-filled to hide that — a newly added ticker starts empty and grows.
    render(<PriceChart ticker="PYPL" quote={null} points={[]} />);

    expect(screen.getByTestId("price-chart")).toHaveTextContent(/Waiting for PYPL prices/);
    expect(screen.getByTestId("price-chart")).toHaveTextContent(/no history behind it/);
  });

  it("renders dashes, not zeros, for a ticker the feed has not priced", () => {
    render(<PriceChart ticker="PYPL" quote={null} points={[]} />);

    expect(screen.getByTestId("chart-price")).toHaveTextContent(EM_DASH);
    expect(screen.getByTestId("chart-day")).toHaveTextContent(EM_DASH);
    expect(screen.getByTestId("chart-low")).toHaveTextContent(EM_DASH);
  });

  it("colours the plot by where the window started", () => {
    const { rerender } = render(
      <PriceChart ticker="AAPL" quote={makeQuote("AAPL", 210)} points={[200, 210]} />,
    );
    expect(document.querySelector("svg.text-up")).not.toBeNull();

    rerender(<PriceChart ticker="AAPL" quote={makeQuote("AAPL", 190)} points={[200, 190]} />);
    expect(document.querySelector("svg.text-down")).not.toBeNull();
  });

  it("flashes the headline price on a change and not on the first one", () => {
    const { rerender, container } = render(
      <PriceChart ticker="AAPL" quote={makeQuote("AAPL", 190)} points={[190]} />,
    );
    expect(container.querySelector(".flash-up, .flash-down")).toBeNull();

    rerender(<PriceChart ticker="AAPL" quote={makeQuote("AAPL", 191)} points={[190, 191]} />);
    expect(screen.getByTestId("chart-price").className).toContain("flash-up");

    rerender(<PriceChart ticker="AAPL" quote={makeQuote("AAPL", 189)} points={[190, 191, 189]} />);
    expect(screen.getByTestId("chart-price").className).toContain("flash-down");
  });

  it("does not let a non-finite point poison the low and high", () => {
    render(
      <PriceChart ticker="AAPL" quote={makeQuote("AAPL", 195)} points={[190, Number.NaN, 195]} />,
    );

    expect(screen.getByTestId("chart-low")).toHaveTextContent("190.00");
    expect(screen.getByTestId("chart-high")).toHaveTextContent("195.00");
  });
});
