import { expect, test } from "@playwright/test";

import { expectFeedStatus, navigationCount, parseMoney, waitForPrice } from "./helpers";

/**
 * PLAN.md §12: "SSE resilience: disconnect and verify reconnection."
 *
 * The connection is dropped with Playwright's own network emulation rather
 * than by stopping the container: the suite runs beside the app in compose
 * with no Docker socket, and — more to the point — this is what a proxy, a
 * sleeping laptop or a dropped WiFi link does to a long-lived response.
 * `EventSource` retries on its own, which is the behaviour being tested.
 *
 * This is the scenario Checkpoint 5 could only verify by driving a browser by
 * hand, and its carried-forward note named this checkpoint as the owner.
 */

/** The number of points drawn in a row's sparkline — one per frame received. */
async function drawnPoints(page: import("@playwright/test").Page): Promise<number> {
  const points = await page
    .getByLabel("AAPL price since page load")
    .locator("polyline")
    .getAttribute("points");
  return points === null ? 0 : points.trim().split(/\s+/).length;
}

test.describe("the price stream", () => {
  test.afterEach(async ({ context }) => {
    // A test that fails mid-outage must not leave the browser offline for the
    // next one — an order dependence that would look like a random failure.
    await context.setOffline(false);
  });

  test("recovers on its own after the connection drops, with no reload", async ({
    page,
    context,
  }) => {
    await page.goto("/");
    await waitForPrice(page, "AAPL");
    await expectFeedStatus(page, "connected");

    const priceBefore = await page.getByTestId("price-AAPL").textContent();

    await context.setOffline(true);

    // Amber first — a one-second blip is not an outage — then red once the
    // grace period passes with no frame.
    await expectFeedStatus(page, "reconnecting", 30_000);
    await expectFeedStatus(page, "disconnected", 30_000);

    // The last known prices stay on screen, and the feed panel says they are
    // the last received rather than blanking the grid.
    await expect(page.getByTestId("price-AAPL")).toHaveText(priceBefore ?? "");
    await expect(page.getByTestId("feed-detail")).toContainText(/last received|no price stream/i);

    await context.setOffline(false);

    // EventSource's own retry does the rest — no reload, no button.
    await expectFeedStatus(page, "connected", 60_000);

    await expect
      .poll(async () => page.getByTestId("price-AAPL").textContent(), {
        message: "prices resume after the reconnection",
        timeout: 30_000,
      })
      .not.toBe(priceBefore);

    expect(await navigationCount(page)).toBe(1);
  });

  test("a reconnection keeps the series accumulated before the outage", async ({
    page,
    context,
  }) => {
    await page.goto("/");
    await waitForPrice(page, "AAPL");

    await expect.poll(() => drawnPoints(page), { timeout: 20_000 }).toBeGreaterThan(4);
    const before = await drawnPoints(page);

    await context.setOffline(true);
    await expectFeedStatus(page, "reconnecting", 30_000);

    // The accumulated history is client-side state, and a dropped connection
    // must not cost it — that is what makes a sparkline survive a blip.
    expect(await drawnPoints(page)).toBeGreaterThanOrEqual(before);

    await context.setOffline(false);
    await expectFeedStatus(page, "connected", 60_000);

    await expect
      .poll(() => drawnPoints(page), { message: "the series resumes growing", timeout: 30_000 })
      .toBeGreaterThan(before);
  });

  test("the header keeps valuing the account from the last known marks", async ({
    page,
    context,
  }) => {
    await page.goto("/");
    await waitForPrice(page, "AAPL");

    const total = parseMoney(await page.getByTestId("header-total").textContent());
    expect(total).not.toBeNull();

    await context.setOffline(true);
    await expectFeedStatus(page, "disconnected", 60_000);

    // PLAN.md §6: only *trading* is refused on a dead feed. Valuation still
    // answers with the last known marks, because a blank portfolio is worse
    // than a stale one.
    expect(parseMoney(await page.getByTestId("header-total").textContent())).toBe(total);
  });
});
