import { describe, expect, it } from "vitest";

import { isTicker, normalizeTicker } from "@/lib/ticker";

describe("isTicker", () => {
  it("accepts the symbols the seeded watchlist is made of", () => {
    for (const symbol of ["AAPL", "GOOGL", "MSFT", "V", "BRK.B", "RDS-A", "X1"]) {
      expect(isTicker(symbol)).toBe(true);
    }
  });

  it("rejects what `TICKER_PATTERN` rejects", () => {
    // The same rule as `backend/app/market/models.py`: a letter first, ten
    // characters at most, and none of the three that would change a URL.
    for (const symbol of ["", "1AAPL", "TOOMANYCHARS", "SPY500!", "A/B", "A:B", "A%B", "AA PL"]) {
      expect(isTicker(symbol)).toBe(false);
    }
  });
});

describe("normalizeTicker", () => {
  it("upper-cases and trims, so the UI sends what the backend stores", () => {
    expect(normalizeTicker("  aapl ")).toBe("AAPL");
  });
});
