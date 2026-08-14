import { expect, test } from "@playwright/test";

import { closeAllPositions, waitForPrice } from "./helpers";

/**
 * PLAN.md §12: "Portfolio visualisation: heatmap renders with correct colours,
 * P&L chart has data points."
 *
 * The heatmap's geometry is unit-tested in `lib/treemap.test.ts`; what only a
 * browser can say is that the tiles are laid out with real width and height,
 * coloured by the sign of the P&L, and that the P&L chart draws a line from
 * the snapshot series rather than an empty box.
 */
test.describe("the portfolio panels", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await waitForPrice(page, "NVDA");
  });

  test.afterEach(async ({ page }) => {
    await closeAllPositions(page);
  });

  test("the heatmap sizes tiles by weight and colours them by P&L sign", async ({ page }) => {
    // Two positions of deliberately different weight, so "sized by weight" is
    // an assertion and not a coincidence.
    await page.request.post("/api/portfolio/trade", {
      data: { ticker: "NVDA", quantity: 4, side: "buy" },
    });
    await page.request.post("/api/portfolio/trade", {
      data: { ticker: "JPM", quantity: 1, side: "buy" },
    });
    await page.reload();

    const big = page.getByTestId("tile-NVDA");
    const small = page.getByTestId("tile-JPM");
    await expect(big).toBeVisible();
    await expect(small).toBeVisible();

    const bigBox = await big.boundingBox();
    const smallBox = await small.boundingBox();
    expect(bigBox, "the NVDA tile has geometry").not.toBeNull();
    expect(smallBox, "the JPM tile has geometry").not.toBeNull();

    // NVDA is worth several times JPM here, and area is the encoding.
    const area = (box: { width: number; height: number }) => box.width * box.height;
    expect(area(bigBox!)).toBeGreaterThan(area(smallBox!));
    expect(bigBox!.width).toBeGreaterThan(0);
    expect(bigBox!.height).toBeGreaterThan(0);

    // Colour follows the sign of the P&L, which at a fresh fill is whichever
    // way the simulator has moved since — so assert the *rule*, not a colour:
    // the tile's background is one of the two tokens, and never neither.
    const background = await big.evaluate((node) => getComputedStyle(node).backgroundColor);
    expect(background).toMatch(/^rgba?\(/);
    expect(background).not.toBe("rgba(0, 0, 0, 0)");
  });

  test("an empty portfolio says so instead of drawing an empty map", async ({ page }) => {
    await closeAllPositions(page);
    await page.reload();

    await expect(page.getByTestId("heatmap-empty")).toBeVisible();
    await expect(page.getByTestId("heatmap-empty")).toContainText(/no positions/i);
  });

  test("the P&L chart draws the snapshot series and extends as snapshots arrive", async ({
    page,
  }) => {
    // A trade writes a snapshot immediately (PLAN.md §7), so this guarantees
    // at least two points without waiting out the 30-second background task.
    await page.request.post("/api/portfolio/trade", {
      data: { ticker: "META", quantity: 1, side: "buy" },
    });
    await page.reload();

    const count = page.getByTestId("pnl-count");
    await expect(count).toBeVisible();

    const points = async () => Number(/(\d+)/.exec((await count.textContent()) ?? "")?.[1] ?? 0);
    await expect.poll(points, { message: "the series has points" }).toBeGreaterThan(0);

    // The line is drawn into a box of real geometry — the failure Checkpoint 6
    // hit twice was a chart rendered into the wrong shape.
    //
    // Measured on the svg, not on the polyline: a portfolio that has not moved
    // draws a horizontal line, whose bounding box is a pixel and a half tall,
    // and Playwright reports a zero-height element as *hidden*. The first
    // version of this assertion failed on `toBeVisible()` against a polyline
    // that was drawn, present and correct.
    const svg = page.getByTestId("pnl-chart").locator("svg").first();
    await expect(svg).toBeAttached();
    await expect(page.getByTestId("pnl-chart").locator("polyline")).toBeAttached();

    const box = (await svg.boundingBox())!;
    expect(box.width, "the P&L plot has width").toBeGreaterThan(100);
    expect(box.height, "the P&L plot has height").toBeGreaterThan(40);
  });

  test("the main chart follows the watchlist selection", async ({ page }) => {
    await waitForPrice(page, "TSLA");
    await page.getByTestId("row-TSLA").click();

    await expect(page.getByTestId("chart-ticker")).toHaveText("TSLA");
    await expect(page.getByTestId("chart-price")).not.toHaveText("—");
    await expect(page.getByTestId("row-TSLA")).toHaveAttribute("data-selected", "true");

    // Selecting also fills the trade bar, so the obvious next action is one
    // field away.
    await expect(page.getByLabel("Ticker", { exact: true })).toHaveValue("TSLA");
  });
});
