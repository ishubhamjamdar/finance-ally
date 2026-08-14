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
/**
 * Viewport width → the floor the price chart must keep.
 *
 * Measured, not guessed. The grid is
 * `minmax(190px,240px) minmax(340px,1fr) minmax(190px,240px) minmax(240px,300px)`
 * below `xl` and roomier above it, so the centre's share is a function of the
 * width — a single floor is either too weak at 1680 or wrong at 1024, where
 * the panel's own padding leaves 314 of the track's 340. The regression these
 * numbers exist to catch rendered **160**.
 */
const MIN_CHART_WIDTH: Record<number, number> = { 1024: 300, 1280: 360, 1680: 560 };

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

      expect(box.width, `chart width at ${width}px`).toBeGreaterThan(MIN_CHART_WIDTH[width]);

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

    // The *plot area*, not the drawn line: a series of one point — or ten that
    // happen to be flat — draws a polyline 1.5 pixels tall, and measuring the
    // line makes this assertion a statement about the simulator's luck. The
    // svg is what has to be given a box.
    const svg = page.getByTestId("price-chart").locator("svg").first();
    const plot = (await svg.boundingBox())!;
    expect(plot.height, "the plot area has vertical extent").toBeGreaterThan(80);
    expect(plot.width, "the plot area is wider than it is tall").toBeGreaterThan(plot.height);

    // Checkpoint 6's second Gate 3 failure: the live-end marker is positioned
    // in CSS while the line is drawn inside the viewBox, so the two only agree
    // if the svg was given both dimensions. A square plot put the marker
    // stranded to the right of where the line stopped.
    // ...once the series is long enough to span the box. `LineChart` maps the
    // points across 0–100 by index, so a series of one draws at x=50 and the
    // marker sits in the middle — correctly. Measuring before the second frame
    // arrives compares the marker to an edge the line has not reached yet.
    await expect
      .poll(
        async () => {
          const points = await page
            .getByTestId("price-chart")
            .locator("polyline")
            .getAttribute("points");
          return points === null ? 0 : points.trim().split(/\s+/).length;
        },
        { message: "the price series spans the plot", timeout: 30_000 },
      )
      .toBeGreaterThan(4);

    const marker = (await page.getByTestId("price-chart-marker").boundingBox())!;
    expect(Math.abs(marker.x + marker.width / 2 - (plot.x + plot.width))).toBeLessThan(16);
  });

  test("collapsing the assistant gives most of the space to the chart", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto("/");
    await waitForPrice(page, "AAPL");

    const railBefore = (await page.getByTestId("row-AAPL").boundingBox())!.width;
    const chartBefore = (await page.getByTestId("price-chart").boundingBox())!.width;

    await page.getByRole("button", { name: "Collapse the assistant" }).click();
    await expect(page.getByTestId("chat-panel-collapsed")).toBeVisible();

    const railAfter = (await page.getByTestId("row-AAPL").boundingBox())!.width;
    const chartAfter = (await page.getByTestId("price-chart").boundingBox())!.width;

    // The rails are `minmax(260px, 320px)`, so they do take some of the freed
    // track — up to their maximum and no further. The first version of this
    // test asserted they did not move at all and failed at 282 → 318, which is
    // the grid behaving exactly as written. What must be true is that the
    // centre, the only `1fr`, gets the bulk of it.
    expect(railAfter).toBeGreaterThanOrEqual(railBefore);
    expect(chartAfter).toBeGreaterThan(chartBefore);
    expect(chartAfter - chartBefore, "the chart gains more than a rail does").toBeGreaterThan(
      railAfter - railBefore,
    );

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
  });
});
