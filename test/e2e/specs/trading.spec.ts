import { expect, test } from "@playwright/test";

import {
  closeAllPositions,
  navigationCount,
  parseMoney,
  readCash,
  tradeFromBar,
  tradeStatus,
  waitForPrice,
} from "./helpers";

/**
 * PLAN.md §12: "Buy shares: cash decreases, position appears, portfolio
 * updates" and "Sell shares: cash increases, position updates or disappears."
 *
 * Every assertion is a *delta*, never an absolute balance: this runs against
 * the shared app, and a spec that asserted "$10,000" would be asserting that
 * nothing else in the suite had traded yet.
 */
test.describe("trading from the trade bar", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await waitForPrice(page, "NVDA");
  });

  test.afterEach(async ({ page }) => {
    await closeAllPositions(page);
  });

  test("a buy moves cash, opens a position, and reaches every panel with no reload", async ({
    page,
  }) => {
    const cashBefore = await readCash(page);

    await tradeFromBar(page, "buy", "NVDA", 3);

    await expect(tradeStatus(page)).toContainText("Bought 3 NVDA at");
    const fill = await tradeStatus(page).textContent();
    const cost = parseMoney(/—\s*(\$[\d,.]+)/.exec(fill ?? "")?.[1] ?? null);
    expect(cost, `a fill value in ${fill}`).not.toBeNull();

    // Cash fell by exactly what the fill said it would. No fees, no slippage
    // (PLAN.md §3), so this is an equality, not a range.
    await expect
      .poll(async () => readCash(page), { message: "cash falls by the fill value" })
      .toBeCloseTo(cashBefore - (cost as number), 2);

    // The position exists in the table, is marked to the live stream, and the
    // heatmap has a tile for it.
    await expect(page.getByTestId("position-NVDA")).toBeVisible();
    await expect(page.getByTestId("position-price-NVDA")).not.toHaveText("—");
    await expect(page.getByTestId("tile-NVDA")).toBeVisible();

    // "with no reload" — the exit criterion Checkpoints 6 and 7 checked by
    // hand in a browser.
    expect(await navigationCount(page)).toBe(1);
  });

  test("a sell returns the cash and removes the row when the position closes", async ({ page }) => {
    await tradeFromBar(page, "buy", "MSFT", 2);
    await expect(page.getByTestId("position-MSFT")).toBeVisible();

    const cashAfterBuy = await readCash(page);

    await tradeFromBar(page, "sell", "MSFT", 2);
    await expect(tradeStatus(page)).toContainText("Sold 2 MSFT at");

    await expect
      .poll(async () => readCash(page), { message: "cash rises on the sell" })
      .toBeGreaterThan(cashAfterBuy);

    // Quantity zero means no row at all — `apply_position` deletes it rather
    // than storing a zero (PLAN.md §7).
    await expect(page.getByTestId("position-MSFT")).toHaveCount(0);
    await expect(page.getByTestId("tile-MSFT")).toHaveCount(0);
  });

  test("a partial sell leaves the position, with the average cost unchanged", async ({ page }) => {
    await tradeFromBar(page, "buy", "AAPL", 4);
    await expect(page.getByTestId("position-AAPL")).toBeVisible();

    // Columns are ticker, quantity, avg cost, price, value, P&L, P&L% — the
    // second cell is the average cost, and a sell must not re-average it
    // (PLAN.md §13, Checkpoint 3's money rules).
    const cells = page.getByTestId("position-AAPL").locator("td");
    await expect(cells.nth(0)).toHaveText("4");
    const avgCost = await cells.nth(1).textContent();

    await tradeFromBar(page, "sell", "AAPL", 1);
    await expect(tradeStatus(page)).toContainText("Sold 1 AAPL at");

    await expect(cells.nth(0)).toHaveText("3");
    await expect(cells.nth(1)).toHaveText(avgCost ?? "");
  });

  test("a rejected trade is visible, and costs nothing", async ({ page }) => {
    const cashBefore = await readCash(page);

    await tradeFromBar(page, "buy", "AAPL", 100_000);

    // The backend's own words, not a generic failure — PLAN.md §8 distinguishes
    // "the account cannot support this" (400) from "malformed" (422), and the
    // panel renders what it was told.
    await expect(tradeStatus(page)).toContainText("Insufficient cash");
    await expect(tradeStatus(page).getByRole("alert")).toBeVisible();

    expect(await readCash(page)).toBeCloseTo(cashBefore, 2);
    await expect(page.getByTestId("position-AAPL")).toHaveCount(0);
  });

  test("selling more than is held is refused in the same way", async ({ page }) => {
    await tradeFromBar(page, "sell", "GOOGL", 5);
    await expect(tradeStatus(page)).toContainText(/hold|position|shares/i);
    await expect(page.getByTestId("position-GOOGL")).toHaveCount(0);
  });
});
