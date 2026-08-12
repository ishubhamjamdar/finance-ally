"use client";

/**
 * The live price feed: one `EventSource` on `/api/stream/prices`, for the life
 * of the mount.
 *
 * ## Reconnection is the browser's job, not ours
 *
 * `EventSource` reconnects by itself, at the cadence the server names in its
 * `retry:` field (1 s — see `backend/app/market/stream.py`). This hook
 * therefore **never constructs a second one**. Opening a fresh connection from
 * the error handler is the classic version of this bug: the old socket is
 * still retrying, so every blip doubles the number of streams the server is
 * feeding, and nothing in the UI looks wrong until the backend is serving
 * thirty copies of the same prices to one page.
 *
 * What we add on top is the *status*, which the browser does not report in the
 * form a user needs:
 *
 * - `connecting`    — first attempt, nothing has arrived yet
 * - `connected`     — the stream is open (green)
 * - `reconnecting`  — an error, and the browser is retrying (yellow)
 * - `disconnected`  — retrying has failed for `RECONNECT_GRACE_MS` (red), or
 *                     the browser gave up altogether
 *
 * The grace period is what makes the dot honest. A dropped connection fires
 * `error` immediately and recovers on the next attempt a second later; going
 * straight to red would make a one-second blip look like an outage, and never
 * going red would leave a dead backend showing amber forever.
 */

import { useEffect, useState } from "react";

import { apiUrl } from "@/lib/api";
import type { MarketShock, PriceFrame, Quote } from "@/lib/types";

export type ConnectionStatus = "connecting" | "connected" | "reconnecting" | "disconnected";

/** Sparkline depth. At the 500 ms tick this is the last minute of price action. */
export const MAX_SPARKLINE_POINTS = 120;

/** How long the browser may keep failing before the dot turns red. */
export const RECONNECT_GRACE_MS = 6000;

/**
 * How long an open connection may deliver nothing before the page says so.
 *
 * A wedged market source is invisible to `EventSource`: the connection stays
 * open and healthy, the server keeps sending heartbeats, and no price ever
 * arrives. Without this the dot reads "Live" over a grid of frozen numbers,
 * which is the one thing the feed panel exists to prevent.
 *
 * It must clear the longest legitimate gap between frames. The simulator sends
 * one every 500 ms and Massive one per poll — 15 s on the free tier — so 30 s
 * is quiet by any default configuration. A deployment that sets
 * `MASSIVE_POLL_INTERVAL` above 30 would need this raised to match.
 */
export const STALL_AFTER_MS = 30_000;

/** How many `event: shock` frames to keep. Oldest are dropped. */
export const MAX_SHOCKS = 12;

export interface PriceStream {
  /** Latest quote per ticker. Empty until the first frame arrives. */
  prices: Record<string, Quote>;
  /**
   * Price series per ticker, accumulated **from page load** — PLAN.md §2. There
   * is no history endpoint behind this and none is faked: a sparkline starts
   * empty and fills in, which is the honest picture of what the client knows.
   */
  sparklines: Record<string, number[]>;
  /** Notable moves, newest first. */
  shocks: MarketShock[];
  status: ConnectionStatus;
  /**
   * Connected, but nothing has arrived for `STALL_AFTER_MS`.
   *
   * Separate from `status` because it is a different failure with a different
   * cause: the connection is fine and the *feed behind it* has stopped. The
   * prices on screen are real, and stale.
   */
  stalled: boolean;
  /** Price frames received this mount. Diagnostic; also proves liveness. */
  frames: number;
  /** `Date.now()` of the last frame, or null before the first. */
  lastFrameAt: number | null;
}

const INITIAL: PriceStream = {
  prices: {},
  sparklines: {},
  shocks: [],
  status: "connecting",
  stalled: false,
  frames: 0,
  lastFrameAt: null,
};

/** How often the stall check runs. Coarse on purpose: it is a wall clock. */
const STALL_CHECK_INTERVAL_MS = 1000;

export function usePriceStream(
  path = "/api/stream/prices",
  stallAfterMs: number = STALL_AFTER_MS,
): PriceStream {
  const [state, setState] = useState<PriceStream>(INITIAL);

  useEffect(() => {
    const source = new EventSource(apiUrl(path));

    // Both of these live exactly as long as the effect does. Keeping them in
    // the closure rather than in refs means there is no way for a stale
    // handler from a previous mount to reach the current timer.
    let status: ConnectionStatus = "connecting";
    let escalation: ReturnType<typeof setTimeout> | null = null;

    // Frame arrival has to be tracked here as well as in state: the stall check
    // runs on a timer with no event to carry the value, and reading it from
    // state would need the effect to re-run — which would reopen the stream.
    let lastFrame: number | null = null;
    let stalled = false;

    const clearEscalation = () => {
      if (escalation !== null) {
        clearTimeout(escalation);
        escalation = null;
      }
    };

    const apply = (next: ConnectionStatus) => {
      if (status === next) return;
      status = next;
      setState((previous) => ({ ...previous, status: next }));
    };

    const onOpen = () => {
      clearEscalation();
      apply("connected");
    };

    const onMessage = (event: MessageEvent<string>) => {
      const frame = parseFrame(event.data);

      // A frame that stored nothing must not advance the counters. The server
      // only sends `data:` when it has prices, so an empty frame here means
      // every quote in it failed validation — and a climbing frame count
      // beside a grid full of dashes reports "the market is quiet" for what is
      // actually a broken feed, which is the one distinction the feed panel
      // exists to make.
      if (frame === null || Object.keys(frame).length === 0) return;

      const receivedAt = Date.now();
      lastFrame = receivedAt;
      if (stalled) {
        stalled = false;
        setState((previous) => ({ ...previous, stalled: false }));
      }

      setState((previous) => {
        const prices = { ...previous.prices };
        const sparklines = { ...previous.sparklines };

        for (const [ticker, quote] of Object.entries(frame)) {
          prices[ticker] = quote;
          sparklines[ticker] = append(previous.sparklines[ticker], quote.price);
        }

        return {
          ...previous,
          prices,
          sparklines,
          frames: previous.frames + 1,
          lastFrameAt: receivedAt,
        };
      });
    };

    const onShock = (event: MessageEvent<string>) => {
      const shock = parseShock(event.data);
      if (shock === null) return;
      setState((previous) => ({
        ...previous,
        shocks: [shock, ...previous.shocks].slice(0, MAX_SHOCKS),
      }));
    };

    const onError = () => {
      // CLOSED means the browser has given up — a wrong content type, or a
      // response it will not retry. Nothing will arrive without a remount.
      if (source.readyState === EventSource.CLOSED) {
        clearEscalation();
        apply("disconnected");
        return;
      }

      // Already red: stay red until something actually opens, rather than
      // flickering back to amber on every retry.
      if (status === "disconnected") return;

      apply("reconnecting");
      if (escalation === null) {
        escalation = setTimeout(() => {
          escalation = null;
          apply("disconnected");
        }, RECONNECT_GRACE_MS);
      }
    };

    // One interval for the life of the mount, not a timeout rescheduled per
    // frame: at two frames a second that would be 7,200 timers an hour.
    const stallCheck = setInterval(() => {
      const next = lastFrame !== null && Date.now() - lastFrame > stallAfterMs;
      if (next === stalled) return;
      stalled = next;
      setState((previous) => ({ ...previous, stalled: next }));
    }, STALL_CHECK_INTERVAL_MS);

    source.addEventListener("open", onOpen);
    source.addEventListener("message", onMessage);
    source.addEventListener("shock", onShock);
    source.addEventListener("error", onError);

    return () => {
      clearEscalation();
      clearInterval(stallCheck);
      source.removeEventListener("open", onOpen);
      source.removeEventListener("message", onMessage);
      source.removeEventListener("shock", onShock);
      source.removeEventListener("error", onError);
      source.close();
    };
  }, [path, stallAfterMs]);

  return state;
}

/** Append a point, dropping the oldest once the window is full. */
function append(series: number[] | undefined, price: number): number[] {
  if (series === undefined) return [price];
  const next = [...series, price];
  return next.length > MAX_SPARKLINE_POINTS ? next.slice(next.length - MAX_SPARKLINE_POINTS) : next;
}

/**
 * A frame is a map of ticker to quote. Anything without a finite `price` is
 * dropped rather than stored: a single `null` reaching `sparklines` poisons
 * every later min/max on that ticker with `NaN`, and the row would render as a
 * dash for the rest of the session.
 */
function parseFrame(data: string): PriceFrame | null {
  const payload = parseJson(data);
  if (payload === null) return null;

  const frame: PriceFrame = {};
  for (const [ticker, quote] of Object.entries(payload)) {
    if (isQuote(quote)) frame[ticker] = quote;
  }
  return frame;
}

function parseShock(data: string): MarketShock | null {
  const payload = parseJson(data);
  if (
    payload === null ||
    typeof payload.ticker !== "string" ||
    !Number.isFinite(payload.magnitude_percent) ||
    !Number.isFinite(payload.price)
  ) {
    return null;
  }
  return payload as unknown as MarketShock;
}

function parseJson(data: string): Record<string, unknown> | null {
  try {
    const payload: unknown = JSON.parse(data);
    if (payload === null || typeof payload !== "object" || Array.isArray(payload)) return null;
    return payload as Record<string, unknown>;
  } catch {
    return null;
  }
}

function isQuote(value: unknown): value is Quote {
  if (value === null || typeof value !== "object") return false;
  const quote = value as Partial<Quote>;
  return typeof quote.ticker === "string" && Number.isFinite(quote.price);
}
