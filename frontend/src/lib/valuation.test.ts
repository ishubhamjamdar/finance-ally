import { describe, expect, it } from "vitest";

import { markPrice, valuePortfolio } from "@/lib/valuation";
import { makePortfolio, makePosition, makeQuote } from "@/test/fixtures";

describe("valuePortfolio", () => {
  it("is null before the portfolio has loaded", () => {
    expect(valuePortfolio(null, {})).toBeNull();
  });

  it("is cash alone for an empty portfolio", () => {
    const value = valuePortfolio(makePortfolio(10000), {});

    expect(value).toEqual({
      positionsValue: 0,
      totalValue: 10000,
      costBasis: 0,
      unpricedTickers: [],
    });
  });

  it("counts the cost of exactly the positions it marked", () => {
    // The endpoint's `cost_basis` covers whichever positions had a price when
    // it was fetched. Once the stream prices one that did not, the two sets
    // diverge — and a P&L percentage built from `positionsValue` over the
    // endpoint's figure reads +55% where the truth is +3.7%.
    const portfolio = makePortfolio(0, [
      makePosition("MSFT", 10, 400, 400),
      makePosition("AAPL", 10, 200, null),
    ]);
    expect(portfolio.cost_basis).toBe(4000); // the backend excludes the unpriced one

    const value = valuePortfolio(portfolio, {
      MSFT: makeQuote("MSFT", 412),
      AAPL: makeQuote("AAPL", 210),
    });

    expect(value?.costBasis).toBe(6000);
    expect(value?.positionsValue).toBeCloseTo(6220, 6);
    expect(value?.unpricedTickers).toEqual([]);
  });

  it("leaves an unpriced position out of the cost basis too", () => {
    const portfolio = makePortfolio(0, [
      makePosition("MSFT", 10, 400, 400),
      makePosition("AAPL", 10, 200, null),
    ]);

    const value = valuePortfolio(portfolio, { MSFT: makeQuote("MSFT", 412) });

    expect(value?.costBasis).toBe(4000);
    expect(value?.unpricedTickers).toEqual(["AAPL"]);
  });

  it("marks every position against the stream", () => {
    const portfolio = makePortfolio(1000, [
      makePosition("AAPL", 10, 180, 190),
      makePosition("MSFT", 2, 400, 410),
    ]);

    const value = valuePortfolio(portfolio, {
      AAPL: makeQuote("AAPL", 200),
      MSFT: makeQuote("MSFT", 420),
    });

    expect(value?.positionsValue).toBeCloseTo(10 * 200 + 2 * 420, 6);
    expect(value?.totalValue).toBeCloseTo(1000 + 2840, 6);
  });

  it("handles fractional quantities", () => {
    const portfolio = makePortfolio(0, [makePosition("AAPL", 0.5, 180, 190)]);

    const value = valuePortfolio(portfolio, { AAPL: makeQuote("AAPL", 201) });

    expect(value?.totalValue).toBeCloseTo(100.5, 6);
  });

  it("excludes an unpriced position from the total and names it", () => {
    // The backend does exactly this, for the reason recorded in PLAN.md §7: a
    // total that silently omits a holding is indistinguishable from a loss.
    const portfolio = makePortfolio(1000, [
      makePosition("AAPL", 10, 180, 190),
      makePosition("XYZ", 5, 50, null),
    ]);

    const value = valuePortfolio(portfolio, { AAPL: makeQuote("AAPL", 200) });

    expect(value?.positionsValue).toBeCloseTo(2000, 6);
    expect(value?.totalValue).toBeCloseTo(3000, 6);
    expect(value?.unpricedTickers).toEqual(["XYZ"]);
  });

  it("never treats an unpriced position as worth zero in a way that hides it", () => {
    const portfolio = makePortfolio(0, [makePosition("XYZ", 5, 50, null)]);

    const value = valuePortfolio(portfolio, {});

    expect(value?.totalValue).toBe(0);
    expect(value?.unpricedTickers).toEqual(["XYZ"]);
  });
});

describe("markPrice", () => {
  it("prefers the streamed price to the fetched one", () => {
    const position = makePosition("AAPL", 1, 180, 190);

    expect(markPrice(position, { AAPL: makeQuote("AAPL", 205) })).toBe(205);
  });

  it("falls back to the fetched mark when the stream has not sent one", () => {
    expect(markPrice(makePosition("AAPL", 1, 180, 190), {})).toBe(190);
  });

  it("is null when neither source has a price", () => {
    expect(markPrice(makePosition("AAPL", 1, 180, null), {})).toBeNull();
  });

  it("does not accept a non-finite streamed price", () => {
    const position = makePosition("AAPL", 1, 180, 190);
    const poisoned = { AAPL: makeQuote("AAPL", Number.NaN) };

    expect(markPrice(position, poisoned)).toBe(190);
  });
});
