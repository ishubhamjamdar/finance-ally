import { defineConfig, devices } from "@playwright/test";

/**
 * The end-to-end suite of PLAN.md §12.
 *
 * It runs against the **container**, never a dev server: `docker-compose.test.yml`
 * builds the production image, starts it with `LLM_MOCK=true`, and runs this
 * suite in a second container on the same network. What is tested is therefore
 * the artefact Checkpoint 8 ships, static export and all.
 *
 * Two app services, and the second one is not redundancy. `app` is shared and
 * mutated — specs buy, sell, and add tickers. `app-pristine` is touched by
 * nothing, which is what lets "a fresh start shows $10,000" assert the real
 * figure instead of hoping it ran first. Checkpoint 9's review focus is
 * flaky-test sources, and order dependence is the one this design removes
 * rather than manages.
 */

/** The shared app: every spec that mutates state uses this. */
const BASE_URL = process.env.BASE_URL ?? "http://localhost:8000";

// The pristine app's URL is read in `specs/helpers.ts`, not exported from
// here: a spec importing the config that loads it is a cycle waiting to bite.

export default defineConfig({
  testDir: "./specs",

  // One worker, always. The app is single-user by design (PLAN.md §7: one
  // profile row, one watchlist), so two parallel workers would be two people
  // trading the same account — and the failure would look like flakiness
  // rather than like the design decision it is.
  workers: 1,
  fullyParallel: false,

  // No retries. A retried assertion about a live price stream hides exactly
  // the intermittency this suite exists to catch; the exit criterion is three
  // consecutive clean runs, not one clean run out of three attempts.
  retries: 0,

  // Generous per-test, tight per-assertion: the container starts a market
  // simulator that needs a tick or two before a price exists, but a locator
  // that never resolves should fail while the reason is still obvious.
  timeout: 60_000,
  expect: { timeout: 15_000 },

  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],

  use: {
    baseURL: BASE_URL,

    // The app is http by design — one container, one port, no TLS (PLAN.md §3)
    // — so nothing here may quietly upgrade a navigation. The service is named
    // `app-shared` rather than `app` for that reason; see the compose file.
    launchOptions: {
      args: ["--disable-features=HttpsUpgrades,HttpsFirstBalancedMode"],
    },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },

  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        // **After** the spread, not in the top-level `use`. Every device
        // descriptor carries its own viewport — `Desktop Chrome` is 1280×720 —
        // and the project's `use` wins, so a viewport declared above was dead
        // config and the whole suite ran at 1280: the width where the `xl`
        // grid sums to exactly its minimums with zero slack. The layout spec
        // sets its own sizes per test; everything else wants headroom.
        viewport: { width: 1440, height: 900 },
      },
    },
  ],
});
