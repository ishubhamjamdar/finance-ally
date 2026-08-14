import { describe, expect, it } from "vitest";

import { squarify } from "@/lib/treemap";

interface Holding {
  ticker: string;
  weight: number | null;
}

function holdings(...weights: [string, number | null][]): Holding[] {
  return weights.map(([ticker, weight]) => ({ ticker, weight }));
}

const weightOf = (holding: Holding) => holding.weight;

/** Do the tiles cover the box exactly once — no gap, no overlap? */
function coveredArea(tiles: { x: number; y: number; width: number; height: number }[]): number {
  return tiles.reduce((total, tile) => total + tile.width * tile.height, 0);
}

function overlaps(
  a: { x: number; y: number; width: number; height: number },
  b: { x: number; y: number; width: number; height: number },
): boolean {
  const epsilon = 1e-9;
  return (
    a.x + a.width > b.x + epsilon &&
    b.x + b.width > a.x + epsilon &&
    a.y + a.height > b.y + epsilon &&
    b.y + b.height > a.y + epsilon
  );
}

describe("squarify", () => {
  it("gives each tile an area proportional to its weight", () => {
    const tiles = squarify(holdings(["AAPL", 0.5], ["MSFT", 0.25], ["NVDA", 0.25]), weightOf);

    const areaOf = (ticker: string) => {
      const tile = tiles.find((candidate) => candidate.item.ticker === ticker);
      if (tile === undefined) throw new Error(`${ticker} got no tile`);
      return tile.width * tile.height;
    };

    expect(areaOf("AAPL")).toBeCloseTo(0.5, 9);
    expect(areaOf("MSFT")).toBeCloseTo(0.25, 9);
    expect(areaOf("NVDA")).toBeCloseTo(0.25, 9);
  });

  it("normalises weights that do not sum to one", () => {
    // The heatmap's weights are shares of the marked positions and do sum to
    // one — but a caller passing raw market values must get the same picture,
    // or the map silently under-fills the box.
    const tiles = squarify(holdings(["AAPL", 6000], ["MSFT", 2000], ["NVDA", 2000]), weightOf);

    expect(coveredArea(tiles)).toBeCloseTo(1, 9);
    expect(tiles[0].width * tiles[0].height).toBeCloseTo(0.6, 9);
  });

  it("fills the box with no gaps and no overlaps", () => {
    const tiles = squarify(
      holdings(["A", 0.31], ["B", 0.22], ["C", 0.17], ["D", 0.13], ["E", 0.09], ["F", 0.08]),
      weightOf,
    );

    expect(coveredArea(tiles)).toBeCloseTo(1, 9);
    for (const tile of tiles) {
      expect(tile.x).toBeGreaterThanOrEqual(-1e-9);
      expect(tile.y).toBeGreaterThanOrEqual(-1e-9);
      expect(tile.x + tile.width).toBeLessThanOrEqual(1 + 1e-9);
      expect(tile.y + tile.height).toBeLessThanOrEqual(1 + 1e-9);
    }
    for (let i = 0; i < tiles.length; i += 1) {
      for (let j = i + 1; j < tiles.length; j += 1) {
        expect(overlaps(tiles[i], tiles[j])).toBe(false);
      }
    }
  });

  it("keeps tiles roughly square rather than slicing the box into ribbons", () => {
    // The entire reason this is squarified and not slice-and-dice. Twelve
    // equal positions cut naively give aspect ratios of 12; squarified they
    // stay near 1, which is what makes a small tile labellable and clickable.
    const equal = holdings(
      ...Array.from({ length: 12 }, (_, index): [string, number] => [`T${index}`, 1]),
    );

    for (const tile of squarify(equal, weightOf)) {
      const ratio = Math.max(tile.width / tile.height, tile.height / tile.width);
      expect(ratio).toBeLessThan(2);
    }
  });

  it("returns tiles largest first", () => {
    const tiles = squarify(holdings(["SMALL", 0.1], ["BIG", 0.7], ["MID", 0.2]), weightOf);

    expect(tiles.map((tile) => tile.item.ticker)).toEqual(["BIG", "MID", "SMALL"]);
  });

  it("gives no tile to a weight of null, zero, or a NaN", () => {
    // An unpriced holding has a null weight, and the heatmap must not draw it
    // as a rectangle of nothing sitting in a corner. It is named under the map
    // instead.
    const tiles = squarify(
      holdings(["AAPL", 1], ["UNPRICED", null], ["ZERO", 0], ["BROKEN", NaN]),
      weightOf,
    );

    expect(tiles).toHaveLength(1);
    expect(tiles[0].item.ticker).toBe("AAPL");
  });

  it("gives a single position the whole box", () => {
    const tiles = squarify(holdings(["AAPL", 0.3]), weightOf);

    expect(tiles).toHaveLength(1);
    expect(tiles[0]).toMatchObject({ x: 0, y: 0, width: 1, height: 1 });
  });

  it("returns nothing for an empty portfolio", () => {
    expect(squarify([], weightOf)).toEqual([]);
    expect(squarify(holdings(["AAPL", null]), weightOf)).toEqual([]);
  });

  it("returns nothing for a box with no area", () => {
    expect(squarify(holdings(["AAPL", 1]), weightOf, 0, 1)).toEqual([]);
    expect(squarify(holdings(["AAPL", 1]), weightOf, 1, -1)).toEqual([]);
  });

  it("honours a box that is not the unit square", () => {
    const tiles = squarify(holdings(["AAPL", 0.5], ["MSFT", 0.5]), weightOf, 200, 100);

    expect(coveredArea(tiles)).toBeCloseTo(200 * 100, 6);
    for (const tile of tiles) {
      expect(tile.x + tile.width).toBeLessThanOrEqual(200 + 1e-9);
      expect(tile.y + tile.height).toBeLessThanOrEqual(100 + 1e-9);
    }
  });
});
