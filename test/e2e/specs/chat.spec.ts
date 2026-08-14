import { expect, test } from "@playwright/test";

import {
  EM_DASH,
  closeAllPositions,
  navigationCount,
  readCash,
  sparklinePoints,
  waitForPrice,
} from "./helpers";

/**
 * PLAN.md §12: "AI chat (mocked): send a message, receive a response, trade
 * execution appears inline."
 *
 * `LLM_MOCK=true` in the compose file, which is §9's stated purpose for the
 * flag: deterministic, free, no key, no network. The mock returns *raw JSON*,
 * so everything downstream of the model — `parse_reply`, `execute_trade`,
 * `watchlist.add`, persistence — is the real code path.
 */
test.describe("the assistant", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await waitForPrice(page, "MSFT");
    // Nothing may be sent before the stored transcript has landed: the panel
    // appends turns rather than re-fetching, and a turn sent first can arrive
    // inside a history response and render twice (Checkpoint 7, review #2).
    await expect(page.getByTestId("chat-transcript")).toBeVisible();
  });

  test.afterEach(async ({ page }) => {
    await closeAllPositions(page);
  });

  test("a message shows a loading indicator, then the reply", async ({ page }) => {
    // The mock answers in milliseconds — faster than the DOM can be sampled —
    // so the response is delayed *in the page* to make the intermediate state
    // observable. The bundle under test is unchanged; only the transport is
    // slowed, which is what Checkpoint 7 did by hand.
    await page.route("**/api/chat", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 1_200));
      return route.continue();
    });

    await page.getByLabel("Message the assistant").fill("How is my portfolio doing?");
    await page.keyboard.press("Enter");

    // The message is in the transcript immediately, the composer is cleared,
    // and the indicator is up while the turn is in flight.
    await expect(page.getByTestId("chat-loading")).toBeVisible();
    await expect(page.getByTestId("chat-turn-user").last()).toContainText("How is my portfolio");
    await expect(page.getByLabel("Message the assistant")).toHaveValue("");

    await expect(page.getByTestId("chat-turn-assistant").last()).toContainText("Mock assistant");
    await expect(page.getByTestId("chat-loading")).toHaveCount(0);
  });

  test("a trade the assistant executes appears inline and moves the portfolio", async ({
    page,
  }) => {
    const cashBefore = await readCash(page);

    await page.getByLabel("Message the assistant").fill("buy 2 MSFT");
    await page.keyboard.press("Enter");

    // Inline, in the backend's own words — not the model's claim about what it
    // did (Checkpoint 7's review focus).
    const action = page.getByTestId("chat-action-ok").last();
    await expect(action).toBeVisible();
    await expect(action).toContainText("buy 2 MSFT");
    await expect(action).toContainText(/Filled 2 MSFT at/);

    // ...and the rest of the page agrees, without a reload.
    await expect(page.getByTestId("position-MSFT")).toBeVisible();
    await expect(page.getByTestId("tile-MSFT")).toBeVisible();
    await expect
      .poll(async () => readCash(page), { message: "cash falls after the assistant's buy" })
      .toBeLessThan(cashBefore);
    expect(await navigationCount(page)).toBe(1);
  });

  test("a refused action is rendered as a refusal, beside the message that asked for it", async ({
    page,
  }) => {
    const cashBefore = await readCash(page);

    await page.getByLabel("Message the assistant").fill("buy 100000 AAPL");
    await page.keyboard.press("Enter");

    // The model composes its message before it knows anything cleared, so the
    // reply cheerfully says it is buying. The outcome goes directly under it,
    // and the transcript would be a lie without this.
    await expect(page.getByTestId("chat-turn-assistant").last()).toContainText("100000 AAPL");
    const failed = page.getByTestId("chat-action-failed").last();
    await expect(failed).toBeVisible();
    await expect(failed).toContainText(/Insufficient cash/i);

    expect(await readCash(page)).toBeCloseTo(cashBefore, 2);
    await expect(page.getByTestId("position-AAPL")).toHaveCount(0);
  });

  test("a watchlist change the assistant makes appears and starts streaming", async ({ page }) => {
    await page.getByLabel("Message the assistant").fill("add PYPL");
    await page.keyboard.press("Enter");

    await expect(page.getByTestId("chat-action-ok").last()).toContainText(/PYPL/);
    await expect(page.getByTestId("row-PYPL")).toBeVisible();
    await expect(page.getByTestId("price-PYPL")).not.toHaveText(EM_DASH, { timeout: 30_000 });

    await page.request.delete("/api/watchlist/PYPL");
  });

  test("the transcript survives a reload", async ({ page }) => {
    const marker = `remember ${Date.now()}`;
    await page.getByLabel("Message the assistant").fill(marker);
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("chat-turn-assistant").last()).toBeVisible();

    await page.reload();

    // Stored, replayed, and still in order.
    await expect(page.getByTestId("chat-transcript")).toContainText(marker);
    await expect(page.getByTestId("chat-turn-assistant").last()).toContainText("Mock assistant");
  });

  test("collapsing and expanding is reversible, and costs no state", async ({ page }) => {
    // Where the space *goes* is `layout.spec.ts`'s question, measured at a
    // pinned viewport. What matters here is that the assistant's own control
    // works both ways and that nothing else is disturbed by using it: the
    // transcript, the accumulated series and the page itself all survive.
    const chartWidthBefore = (await page.getByTestId("price-chart").boundingBox())!.width;
    const pointsBefore = await sparklinePoints(page, "AAPL");

    await page.getByRole("button", { name: "Collapse the assistant" }).click();
    await expect(page.getByTestId("chat-panel")).toHaveCount(0);
    await expect(page.getByTestId("chat-panel-collapsed")).toBeVisible();
    expect((await page.getByTestId("price-chart").boundingBox())!.width).toBeGreaterThan(
      chartWidthBefore,
    );

    await page.getByRole("button", { name: "Expand the assistant" }).click();
    await expect(page.getByTestId("chat-panel")).toBeVisible();
    expect((await page.getByTestId("price-chart").boundingBox())!.width).toBeCloseTo(
      chartWidthBefore,
      0,
    );

    // The stream ran throughout — the series only ever grows — and none of it
    // involved reloading.
    expect(await sparklinePoints(page, "AAPL")).toBeGreaterThanOrEqual(pointsBefore);
    expect(await navigationCount(page)).toBe(1);
  });
});
