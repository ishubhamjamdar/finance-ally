import { expect, test, type Page } from "@playwright/test";

import {
  expectFeedStatus,
  navigationCount,
  parseMoney,
  sparklinePoints,
  waitForPrice,
} from "./helpers";

/**
 * PLAN.md §12: "SSE resilience: disconnect and verify reconnection."
 *
 * **How the connection is broken, and why it took three attempts.**
 *
 * `context.setOffline(true)` does not do it: Chromium's offline emulation
 * blocks *new* requests, and an established `text/event-stream` response is
 * neither new nor retried. The indicator stayed "connected" for the full
 * thirty seconds — correctly, because nothing had told the client otherwise.
 * Dispatching an `offline` event does not do it either: `usePriceStream`
 * listens to `EventSource`, not to `window`.
 *
 * What does: **serving the stream from the test**. `route.fulfill` answers the
 * `EventSource` request with real frames and then *ends the body*, which is
 * precisely what a server-side disconnect looks like to the client — the
 * browser fires `error` and starts retrying on its own. Retries are refused
 * for the length of the outage; then the route is removed and the next retry
 * reaches the real server. Every state the dot passes through is the
 * application's, driven by `EventSource`'s built-in retry, with no reload and
 * no test-only code in the bundle.
 *
 * What this does *not* cover: severing a connection that the real server is
 * feeding. From inside the browser there is no way to do it, and the client
 * cannot tell that case from this one — both arrive as one `error` event. A
 * test that stops the container mid-stream would cover it and needs a Docker
 * socket the runner deliberately does not have; it is carried forward.
 *
 * This is the scenario Checkpoint 5 could only verify by driving a browser by
 * hand, and its carried-forward note named this checkpoint as the owner.
 */

const STREAM = "**/api/stream/prices";

/**
 * A `data:` frame in the wire format `app/market/stream.py` emits: a **map
 * keyed by ticker**, not a list under a `prices` key.
 *
 * Worth stating, because the first version of this helper invented
 * `{"prices": [...]}` and every frame was discarded by `parseFrame` — the
 * client behaved exactly as designed for a feed sending it nothing usable, and
 * the spec failed two tests down with "no points accumulated". A fabricated
 * frame is a fixture, and a fixture that does not match the wire is a test of
 * itself.
 */
function frame(ticker: string, price: number): string {
  // Epoch **seconds**, as `PriceUpdate.to_dict` emits and `formatClock`
  // multiplies back up — not an ISO string. `isQuote` validates only the
  // ticker and the price, so an ISO string is accepted and every assertion
  // here still passes, while the feed panel's "last update" renders a dash
  // from a NaN date. The docstring above makes the case; this is the same
  // class of defect, one that happens not to bite.
  const now = Date.now() / 1000;
  return `data: ${JSON.stringify({
    [ticker]: {
      ticker,
      price,
      previous_price: price,
      timestamp: now,
      direction: "flat",
      previous_close: price,
      change: 0,
      change_percent: 0,
      day_change: 0,
      day_change_percent: 0,
    },
  })}\n\n`;
}

/**
 * Serve `count` frames and then end the response — an open connection that
 * drops — and refuse every retry after that.
 *
 * Returns the outage: call `end()` to stop refusing, after which the client's
 * next retry reaches the real server.
 */
async function serveThenDrop(page: Page, count: number) {
  let served = 0;
  await page.route(STREAM, async (route) => {
    served += 1;
    if (served > 1) return route.abort("connectionfailed");

    const body = Array.from({ length: count }, (_, index) =>
      frame("AAPL", 100 + index),
    ).join("");
    return route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream", "cache-control": "no-cache" },
      body,
    });
  });

  return { end: () => page.unroute(STREAM) };
}

test.describe("the price stream", () => {
  test("recovers on its own after the connection drops, with no reload", async ({ page }) => {
    const outage = await serveThenDrop(page, 3);
    await page.goto("/");

    // It really did connect, and the proof is the frames on screen rather than
    // the indicator: a fulfilled body arrives and ends in the same millisecond,
    // so "connected" is a state the DOM passes through faster than it can be
    // sampled. Asserting on it made this test a race.
    await expect(page.getByTestId("price-AAPL")).toHaveText("102.00");

    // Then the body ended. Amber first — a blip is not an outage — and red
    // once the grace period passes with no frame.
    await expectFeedStatus(page, "reconnecting", 45_000);
    await expectFeedStatus(page, "disconnected", 45_000);

    // The last known price stays on screen, and the panel says so rather than
    // blanking the grid.
    await expect(page.getByTestId("price-AAPL")).toHaveText("102.00");
    await expect(page.getByTestId("feed-detail")).toContainText(/last received|no price stream/i);

    await outage.end();

    // EventSource's own retry does the rest — no reload, no button.
    await expectFeedStatus(page, "connected", 60_000);
    await expect
      .poll(async () => page.getByTestId("price-AAPL").textContent(), {
        message: "real prices resume after the reconnection",
        timeout: 30_000,
      })
      .not.toBe("102.00");

    expect(await navigationCount(page)).toBe(1);
  });

  test("a reconnection keeps the series accumulated before the outage", async ({ page }) => {
    const outage = await serveThenDrop(page, 6);
    await page.goto("/");

    // Six frames in, six points drawn.
    await expect
      .poll(() => sparklinePoints(page, "AAPL"), { timeout: 30_000 })
      .toBeGreaterThanOrEqual(6);
    const before = await sparklinePoints(page, "AAPL");

    await expectFeedStatus(page, "reconnecting", 45_000);

    // The accumulated history is client-side state, and a dropped connection
    // must not cost it — that is what makes a sparkline survive a blip.
    expect(await sparklinePoints(page, "AAPL")).toBeGreaterThanOrEqual(before);

    await outage.end();
    await expectFeedStatus(page, "connected", 60_000);

    await expect
      .poll(() => sparklinePoints(page, "AAPL"), {
        message: "the series resumes growing rather than restarting",
        timeout: 30_000,
      })
      .toBeGreaterThan(before);
  });

  test("the header keeps valuing the account while the feed is down", async ({ page }) => {
    await page.goto("/");
    await waitForPrice(page, "AAPL");

    const total = parseMoney(await page.getByTestId("header-total").textContent());
    expect(total).not.toBeNull();

    // Refuse every reconnection from here. The open stream stays up until the
    // server ends it, so this reaches "disconnected" only if the connection
    // does drop — which is why the assertion below is about the *value*, not
    // about the dot: PLAN.md §6 says valuation answers from the last known
    // marks whatever the feed is doing, and that must hold in both states.
    await page.route(STREAM, (route) => route.abort("connectionfailed"));

    await expect
      .poll(async () => parseMoney(await page.getByTestId("header-total").textContent()), {
        message: "the total is still a number",
        timeout: 20_000,
      })
      .not.toBeNull();

    await expect(page.getByTestId("header-cash")).not.toHaveText("—");
    await page.unroute(STREAM);
  });
});
