import { expect, test } from "@playwright/test";

import { DEFAULT_WATCHLIST, EM_DASH, PRISTINE_URL, parseMoney, waitForPrice } from "./helpers";

/**
 * PLAN.md §12: "Fresh start: default watchlist appears, $10k balance shown,
 * prices are streaming."
 *
 * Against `app-pristine`, a second container of the same image that no other
 * spec touches. Asserting $10,000 against the shared app would mean asserting
 * that this file happens to run before the ones that trade — which is true
 * today, by alphabet, and is exactly the order dependence this checkpoint's
 * review focus names as a flaky-test source.
 */
test.use({ baseURL: PRISTINE_URL });

test.describe("a fresh start", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
  });

  test("seeds the ten default tickers, in the order PLAN.md §7 lists them", async ({ page }) => {
    for (const ticker of DEFAULT_WATCHLIST) {
      await expect(page.getByTestId(`row-${ticker}`)).toBeVisible();
    }

    const symbols = await page
      .getByRole("rowheader")
      .filter({ hasNotText: /^$/ })
      .allInnerTexts();
    expect(symbols.map((text) => text.trim())).toEqual(DEFAULT_WATCHLIST);
  });

  test("shows the $10,000 the profile is seeded with", async ({ page }) => {
    await expect(page.getByTestId("header-cash")).toHaveText("$10,000.00");
  });

  test("has no positions, and says so rather than drawing an empty box", async ({ page }) => {
    await expect(page.getByTestId("heatmap-empty")).toBeVisible();
    await expect(page.getByTestId("heatmap")).toHaveCount(0);
  });

  test("streams prices: every default ticker is priced, and the total follows", async ({
    page,
  }) => {
    await waitForPrice(page, "AAPL");

    for (const ticker of DEFAULT_WATCHLIST) {
      await expect(page.getByTestId(`price-${ticker}`)).not.toHaveText(EM_DASH);
    }

    // With no positions the total is the cash balance, so this asserts the
    // header is reading the live valuation rather than a fetched constant.
    const total = parseMoney(await page.getByTestId("header-total").textContent());
    expect(total).toBe(10_000);
  });

  test("the feed reports itself live", async ({ page }) => {
    await expect(page.getByLabel(/^Market feed:/)).toHaveAttribute("data-status", "connected");
  });

  test("prices move, and a moving price flashes", async ({ page }) => {
    // The one assertion Checkpoint 5 could only make by hand: the flash class
    // is applied on a change, and it is removed again rather than sticking.
    const first = await waitForPrice(page, "TSLA");
    const cell = page.getByTestId("price-TSLA");

    await expect
      .poll(async () => parseMoney(await cell.textContent()), {
        message: "TSLA price changed",
        timeout: 20_000,
      })
      .not.toBe(first);

    // A flash somewhere in the grid within a few ticks. Not TSLA specifically:
    // a cell can re-render with the same price, and pinning the assertion to
    // one row would be asserting the simulator's luck.
    await expect
      .poll(async () => page.locator("[class*='flash-']").count(), {
        message: "a price cell carrying a flash class",
        timeout: 20_000,
      })
      .toBeGreaterThan(0);

    // ...and it fades. The class is removed once the animation window passes,
    // which is what "fading rather than sticking" means in the DOM.
    await expect
      .poll(async () => page.locator("[class*='flash-']").count(), {
        message: "flash classes clear again",
        timeout: 20_000,
      })
      .toBe(0);
  });

  test("sparklines accumulate from page load rather than arriving full", async ({ page }) => {
    const sparkline = page.getByLabel("AAPL price since page load");
    await expect(sparkline).toBeVisible();

    // Counted from the drawn polyline, because the row passes its own
    // aria-label and the point count is not in it. One coordinate pair per
    // frame this page has received.
    const drawnPoints = async () => {
      const points = await sparkline.locator("polyline").getAttribute("points");
      return points === null ? 0 : points.trim().split(/\s+/).length;
    };

    // The series is what *this* page has seen since load, not a history the
    // client was handed: it starts small and grows one point per tick.
    const early = await drawnPoints();
    expect(early).toBeLessThan(60);

    await expect
      .poll(drawnPoints, { message: "sparkline points accumulate", timeout: 20_000 })
      .toBeGreaterThan(early);
  });
});
