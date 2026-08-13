import { describe, expect, it } from "vitest";

import { markPositions, markPrice, valuePortfolio } from "@/lib/valuation";
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

describe("markPositions", () => {
  it("marks every figure against the stream, not the fetched price", () => {
    const portfolio = makePortfolio(0, [makePosition("AAPL", 10, 180, 190)]);

    const [position] = markPositions(portfolio, { AAPL: makeQuote("AAPL", 200) });

    expect(position.price).toBe(200);
    expect(position.marketValue).toBe(2000);
    expect(position.costBasis).toBe(1800);
    expect(position.unrealizedPnl).toBe(200);
    expect(position.unrealizedPnlPercent).toBeCloseTo(11.111, 3);
  });

  it("weights each position by its share of the marked value", () => {
    const portfolio = makePortfolio(0, [
      makePosition("AAPL", 10, 100, 100),
      makePosition("MSFT", 10, 100, 100),
      makePosition("NVDA", 20, 100, 100),
    ]);

    const weights = Object.fromEntries(
      markPositions(portfolio, {
        AAPL: makeQuote("AAPL", 100),
        MSFT: makeQuote("MSFT", 100),
        NVDA: makeQuote("NVDA", 100),
      }).map((position) => [position.ticker, position.weight]),
    );

    expect(weights.AAPL).toBeCloseTo(0.25, 9);
    expect(weights.MSFT).toBeCloseTo(0.25, 9);
    expect(weights.NVDA).toBeCloseTo(0.5, 9);
  });

  it("leaves an unpriced holding null throughout rather than zeroing it", () => {
    // The rule the heatmap depends on: a null weight gets no tile, and a zero
    // weight would get a tile of nothing sitting in the corner claiming the
    // position is worthless.
    const portfolio = makePortfolio(0, [
      makePosition("MSFT", 10, 400, 400),
      makePosition("AAPL", 10, 200, null),
    ]);

    const marked = markPositions(portfolio, { MSFT: makeQuote("MSFT", 400) });
    const aapl = marked.find((position) => position.ticker === "AAPL");

    expect(aapl?.price).toBeNull();
    expect(aapl?.marketValue).toBeNull();
    expect(aapl?.unrealizedPnl).toBeNull();
    expect(aapl?.unrealizedPnlPercent).toBeNull();
    expect(aapl?.weight).toBeNull();
    // The priced one still gets the whole weight — the map fills the box.
    expect(marked.find((position) => position.ticker === "MSFT")?.weight).toBeCloseTo(1, 9);
  });

  it("keeps the quantity and cost of an unpriced holding, so the row survives", () => {
    const portfolio = makePortfolio(0, [makePosition("AAPL", 3, 200, null)]);

    const [position] = markPositions(portfolio, {});

    expect(position.quantity).toBe(3);
    expect(position.avgCost).toBe(200);
    expect(position.costBasis).toBe(600);
  });

  it("has no weights at all when nothing can be marked", () => {
    const portfolio = makePortfolio(0, [makePosition("AAPL", 3, 200, null)]);

    expect(markPositions(portfolio, {})[0].weight).toBeNull();
  });

  it("preserves the order the endpoint returned", () => {
    const portfolio = makePortfolio(0, [
      makePosition("ZZZ", 1, 10, 10),
      makePosition("AAA", 1, 10, 10),
    ]);

    expect(markPositions(portfolio, {}).map((position) => position.ticker)).toEqual(["ZZZ", "AAA"]);
  });

  it("is empty before the portfolio has loaded", () => {
    expect(markPositions(null, {})).toEqual([]);
  });
});
