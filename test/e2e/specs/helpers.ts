import { expect, type Locator, type Page } from "@playwright/test";

/** What `lib/format.ts` renders for anything unknown. Never "0.00". */
export const EM_DASH = "—";

/**
 * The second app container, which no spec mutates.
 *
 * Read from the environment here rather than imported from the config, so a
 * spec never imports the file that imports it.
 */
export const PRISTINE_URL = process.env.PRISTINE_URL ?? "http://localhost:8001";

/** The ten tickers PLAN.md §7 seeds. */
export const DEFAULT_WATCHLIST = [
  "AAPL",
  "GOOGL",
  "MSFT",
  "AMZN",
  "TSLA",
  "NVDA",
  "META",
  "JPM",
  "V",
  "NFLX",
];

/**
 * `"$9,430.18"` → `9430.18`, and `"—"` → `null`.
 *
 * Every money assertion goes through this rather than comparing strings: the
 * header renders thousands separators and the total is rounded to whole
 * dollars, so `toHaveText("$9430.18")` would be asserting the formatter.
 */
export function parseMoney(text: string | null): number | null {
  if (text === null) return null;
  const cleaned = text.replace(/[$,\s]/g, "");
  if (cleaned === EM_DASH || cleaned === "") return null;
  const value = Number(cleaned);
  return Number.isFinite(value) ? value : null;
}

/** The cash figure in the header, as a number. */
export async function readCash(page: Page): Promise<number> {
  const text = await page.getByTestId("header-cash").textContent();
  const value = parseMoney(text);
  expect(value, `header cash was ${text}`).not.toBeNull();
  return value as number;
}

/** The price shown for `ticker` in the watchlist, or null while it is a dash. */
export async function readPrice(page: Page, ticker: string): Promise<number | null> {
  return parseMoney(await page.getByTestId(`price-${ticker}`).textContent());
}

/**
 * Wait until the stream has priced a ticker.
 *
 * Nothing in this suite may assert on a price before this resolves: the
 * container starts its simulator when the app starts, and the first frame is
 * up to one tick away. Waiting for the *condition* rather than sleeping is the
 * rule Checkpoint 5's follow-up pass established after a fixed sleep turned
 * out to be the flake it had been hunting for two checkpoints.
 */
export async function waitForPrice(page: Page, ticker: string): Promise<number> {
  const cell = page.getByTestId(`price-${ticker}`);
  await expect(cell).not.toHaveText(EM_DASH, { timeout: 30_000 });
  const price = parseMoney(await cell.textContent());
  expect(price, `${ticker} priced`).not.toBeNull();
  return price as number;
}

/**
 * The feed indicator. `data-status` lives on the labelled wrapper, not on the
 * dot itself, and the accessible name is the same information — which is the
 * point of the component: "is the feed up?" must not require telling amber
 * from red.
 */
export function feedIndicator(page: Page): Locator {
  return page.getByLabel(/^Market feed:/);
}

/** Wait for the feed indicator to reach `status` (its `data-status` value). */
export async function expectFeedStatus(
  page: Page,
  status: string,
  timeout = 30_000,
): Promise<void> {
  await expect(feedIndicator(page)).toHaveAttribute("data-status", status, { timeout });
}

/**
 * Buy or sell through the trade bar, exactly as a user does.
 *
 * Deliberately not an API call: a spec that posts to `/api/portfolio/trade`
 * and then asserts the panels updated is testing the backend twice and the
 * wiring not at all.
 */
export async function tradeFromBar(
  page: Page,
  side: "buy" | "sell",
  ticker: string,
  quantity: number,
): Promise<void> {
  await page.getByLabel("Ticker", { exact: true }).fill(ticker);
  await page.getByLabel("Quantity", { exact: true }).fill(String(quantity));
  await page.getByRole("button", { name: side === "buy" ? "Buy" : "Sell", exact: true }).click();
}

/** The trade bar's status line — the fill confirmation or the refusal. */
export function tradeStatus(page: Page): Locator {
  return page.getByTestId("trade-status");
}

/**
 * Sell everything held, so a spec leaves the account as it found it.
 *
 * The database is shared across specs within a run (one container, one
 * profile), so a spec that opens a position and leaves it changes what the
 * next one sees. This is the cleanup half of that contract; the other half is
 * that no spec asserts an absolute cash figure against the shared app.
 */
export async function closeAllPositions(page: Page): Promise<void> {
  const response = await page.request.get("/api/portfolio");
  const portfolio = (await response.json()) as {
    positions: { ticker: string; quantity: number }[];
  };

  for (const position of portfolio.positions) {
    await page.request.post("/api/portfolio/trade", {
      data: { ticker: position.ticker, quantity: position.quantity, side: "sell" },
    });
  }
}

/**
 * How many navigations this page has performed.
 *
 * Every "without a reload" assertion in PLAN.md's exit criteria comes down to
 * this number staying at 1 — the same check Checkpoints 6 and 7 made by hand
 * in a browser, now written down.
 */
export async function navigationCount(page: Page): Promise<number> {
  return page.evaluate(() => performance.getEntriesByType("navigation").length);
}
