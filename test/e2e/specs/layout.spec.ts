import { expect, test } from "@playwright/test";

import { waitForPrice } from "./helpers";

/**
 * The carried-forward item Checkpoints 6 and 7 both named this checkpoint for.
 *
 * Three layout defects shipped to Gate 3 between them — a panel collapsed to
 * its content height, a plot the browser sized from its `viewBox` into a
 * square, and a price chart squeezed to 160 pixels by a fourth column — and
 * **none was visible to jsdom**, which performs no layout. Each was found by
 * measuring a real browser by hand. This is those measurements, written down.
 *
 * The assertions are floors and invariants, not pixel snapshots: a test that
 * pins exact widths fails on every deliberate change and teaches nothing.
 */
const WIDTHS = [1024, 1280, 1680] as const;

/** Everything §10 requires on screen, by the hook each panel already carries. */
const PANELS = [
  "header-total",
  "row-AAPL",
  "price-chart",
  "trade-status",
  "chat-panel",
  "feed-detail",
] as const;

test.describe("the workstation layout", () => {
  for (const width of WIDTHS) {
    test(`at ${width}px every panel is on screen and nothing overflows`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/");
      await waitForPrice(page, "AAPL");

      for (const panel of PANELS) {
        await expect(page.getByTestId(panel), `${panel} at ${width}px`).toBeVisible();
      }

      // The page must not scroll sideways at any supported width. This is the
      // check that would have caught the 160-pixel chart: the centre column
      // was not overflowing, it was being squeezed, and the two failures look
      // the same to a reader and different to a measurement.
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, "horizontal overflow").toBeLessThanOrEqual(1);
    });

    test(`at ${width}px the price chart keeps a usable width`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/");
      await waitForPrice(page, "AAPL");

      const chart = page.getByTestId("price-chart");
      const box = (await chart.boundingBox())!;

      // 320px is the floor `minmax(340px, 1fr)` exists to hold, less the
      // panel's own padding. At 1200px the regression rendered 160.
      expect(box.width, `chart width at ${width}px`).toBeGreaterThan(320);

      // ...and it is a *plot*, not a square: the SVG is stretched with
      // preserveAspectRatio="none" into whatever box it is given, so a chart
      // taller than it is wide means the box was never sized.
      expect(box.width).toBeGreaterThan(box.height);
    });
  }

  test("panels fill their column rather than collapsing to their content", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await waitForPrice(page, "AAPL");

    // Checkpoint 6's first Gate 3 failure: a flex item stretches on the cross
    // axis, not the main one, so the chart rendered 83px tall inside a 924px
    // column and the plot inside it 19px.
    const chart = (await page.getByTestId("price-chart").boundingBox())!;
    expect(chart.height, "the chart panel fills its share of the column").toBeGreaterThan(200);

    const line = page.getByTestId("price-chart").locator("polyline").first();
    const plot = (await line.boundingBox())!;
    expect(plot.height, "the plotted line has vertical extent").toBeGreaterThan(20);

    // Checkpoint 6's second: the live-end marker is positioned in CSS while
    // the line is drawn in the viewBox, so they only agree if the SVG was
    // given both dimensions. They must land in the same place.
    const marker = (await page.getByTestId("price-chart-marker").boundingBox())!;
    expect(Math.abs(marker.x - (plot.x + plot.width))).toBeLessThan(12);
  });

  test("collapsing the assistant gives the space to the chart, not to the rails", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto("/");
    await waitForPrice(page, "AAPL");

    const railBefore = (await page.getByTestId("row-AAPL").boundingBox())!.width;
    const chartBefore = (await page.getByTestId("price-chart").boundingBox())!.width;

    await page.getByRole("button", { name: "Collapse the assistant" }).click();
    await expect(page.getByTestId("chat-panel-collapsed")).toBeVisible();

    expect((await page.getByTestId("row-AAPL").boundingBox())!.width).toBeCloseTo(railBefore, 0);
    expect((await page.getByTestId("price-chart").boundingBox())!.width).toBeGreaterThan(
      chartBefore,
    );

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
  });
});
