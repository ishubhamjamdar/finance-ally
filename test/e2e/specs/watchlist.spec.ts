import { expect, test } from "@playwright/test";

import { EM_DASH, closeAllPositions, navigationCount, waitForPrice } from "./helpers";

/**
 * PLAN.md §12: "Add and remove a ticker from the watchlist."
 *
 * The interesting half is the streaming: `POST /api/watchlist` also calls
 * `add_ticker()` on the live source, and a row that never gets a price is the
 * defect that split is there to prevent (MARKET_DATA_DESIGN.md §13.4).
 */
const ADDED = "PYPL";

test.describe("the watchlist", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await waitForPrice(page, "AAPL");
  });

  test.afterEach(async ({ page }) => {
    // Leave the list as it was found, whatever the test did to it.
    await page.request.delete(`/api/watchlist/${ADDED}`);
    await closeAllPositions(page);
  });

  test("an added ticker appears and starts streaming", async ({ page }) => {
    await page.getByLabel("Add ticker").fill(ADDED);
    await page.getByRole("button", { name: "Add to watchlist" }).click();

    await expect(page.getByTestId(`row-${ADDED}`)).toBeVisible();

    // The row is not enough: it has to be priced, which only happens if the
    // handler told the market source about it.
    await expect(page.getByTestId(`price-${ADDED}`)).not.toHaveText(EM_DASH, { timeout: 30_000 });

    // ...and it keeps being priced, i.e. it joined the stream rather than
    // getting one value from the initial fetch.
    const first = await page.getByTestId(`price-${ADDED}`).textContent();
    await expect
      .poll(async () => page.getByTestId(`price-${ADDED}`).textContent(), {
        message: `${ADDED} keeps ticking`,
        timeout: 20_000,
      })
      .not.toBe(first);

    expect(await navigationCount(page)).toBe(1);
  });

  test("adding one that is already there reports the duplicate, and keeps the symbol", async ({
    page,
  }) => {
    await page.getByLabel("Add ticker").fill("AAPL");
    await page.getByRole("button", { name: "Add to watchlist" }).click();

    await expect(page.getByTestId("watchlist-error")).toContainText(/already/i);
    // The field keeps what was typed, so a near-miss can be corrected rather
    // than retyped.
    await expect(page.getByLabel("Add ticker")).toHaveValue("AAPL");
    await expect(page.getByTestId("row-AAPL")).toHaveCount(1);
  });

  test("removing a ticker drops the row without touching a position held in it", async ({
    page,
  }) => {
    // Hold the ticker being removed. PLAN.md §8: DELETE does not sell, and a
    // held ticker stays subscribed so the position keeps a live mark.
    await page.request.post("/api/portfolio/trade", {
      data: { ticker: "TSLA", quantity: 1, side: "buy" },
    });
    await page.reload();
    await expect(page.getByTestId("position-TSLA")).toBeVisible();

    await page.getByRole("button", { name: "Remove TSLA from the watchlist" }).click();

    await expect(page.getByTestId("row-TSLA")).toHaveCount(0);

    // The position survives, and is still being marked to a live price.
    await expect(page.getByTestId("position-TSLA")).toBeVisible();
    await expect(page.getByTestId("position-price-TSLA")).not.toHaveText(EM_DASH);

    const price = await page.getByTestId("position-price-TSLA").textContent();
    await expect
      .poll(async () => page.getByTestId("position-price-TSLA").textContent(), {
        message: "a held ticker keeps its price feed after leaving the watchlist",
        timeout: 20_000,
      })
      .not.toBe(price);

    // Put it back, so the next spec sees the seeded ten.
    await page.getByLabel("Add ticker").fill("TSLA");
    await page.getByRole("button", { name: "Add to watchlist" }).click();
    await expect(page.getByTestId("row-TSLA")).toBeVisible();
  });

  test("a symbol the pattern refuses never reaches the server", async ({ page }) => {
    // Only the POST counts: the panel re-reads the list on other events, and
    // counting every request to the path would flag a GET as a failed guard.
    let posted = false;
    await page.route("**/api/watchlist", (route, request) => {
      if (request.method() === "POST") posted = true;
      return route.continue();
    });

    await page.getByLabel("Add ticker").fill("not a ticker!");
    await page.getByRole("button", { name: "Add to watchlist" }).click();

    await expect(page.getByTestId("watchlist-error")).toBeVisible();
    expect(posted).toBe(false);
  });
});
