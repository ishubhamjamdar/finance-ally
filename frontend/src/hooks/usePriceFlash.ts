"use client";

/**
 * The green/red flash on a price change — PLAN.md §2.
 *
 * Returns the direction of the last change and a sequence number. The
 * direction picks the CSS class; the sequence is meant to be used as the
 * element's `key`, which forces React to replace the node so the animation
 * restarts. Without it, a second move arriving inside the 500 ms window would
 * land on an element already running the animation and produce no flash at
 * all — exactly when the market is busiest and the flash matters most.
 */

import { useEffect, useRef, useState } from "react";

import type { Direction } from "@/lib/types";

/** Matches the `flash-up` / `flash-down` animations in `globals.css`. */
export const FLASH_MS = 500;

export interface Flash {
  /** "up", "down", or null once the flash has faded. */
  direction: Exclude<Direction, "flat"> | null;
  /** Increments on every change. Use as a React `key` to restart the animation. */
  seq: number;
}

const IDLE: Flash = { direction: null, seq: 0 };

export function usePriceFlash(price: number | null | undefined, durationMs = FLASH_MS): Flash {
  const previous = useRef(price);
  const [flash, setFlash] = useState<Flash>(IDLE);

  useEffect(() => {
    const prior = previous.current;
    previous.current = price;

    // The first price a row ever sees is not a change. Flashing on it would
    // light the whole watchlist green on page load.
    if (price == null || prior == null || price === prior) return;

    setFlash((current) => ({ direction: price > prior ? "up" : "down", seq: current.seq + 1 }));

    const timer = setTimeout(() => {
      setFlash((current) => ({ ...current, direction: null }));
    }, durationMs);

    return () => clearTimeout(timer);
  }, [price, durationMs]);

  // A price that goes away is not a fall — it is an unknown, and the row
  // renders a dash. Derived here rather than cleared inside the effect: the
  // effect's early return would otherwise leave the last direction set for the
  // rest of the session, and clearing it there means a second state
  // transition, scheduled a render late, for something already known now.
  return price == null ? { direction: null, seq: flash.seq } : flash;
}

/** The class to put on the flashing element, or "" when it is not flashing. */
export function flashClass(flash: Flash): string {
  if (flash.direction === "up") return "flash-up";
  if (flash.direction === "down") return "flash-down";
  return "";
}
