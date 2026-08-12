import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FLASH_MS, flashClass, usePriceFlash } from "@/hooks/usePriceFlash";

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("usePriceFlash", () => {
  it("does not flash on the first price it sees", () => {
    const { result } = renderHook(({ price }) => usePriceFlash(price), {
      initialProps: { price: null as number | null },
    });

    expect(result.current.direction).toBeNull();
  });

  it("does not flash when a row mounts with a price already set", () => {
    // Otherwise the whole watchlist lights up green on page load.
    const { result } = renderHook(() => usePriceFlash(190.5));

    expect(result.current.direction).toBeNull();
  });

  it("flashes up on a rise and down on a fall", () => {
    const { result, rerender } = renderHook(({ price }) => usePriceFlash(price), {
      initialProps: { price: 100 },
    });

    rerender({ price: 101 });
    expect(result.current.direction).toBe("up");

    act(() => {
      vi.advanceTimersByTime(FLASH_MS);
    });

    rerender({ price: 99 });
    expect(result.current.direction).toBe("down");
  });

  it("does not flash when the price is unchanged", () => {
    const { result, rerender } = renderHook(({ price }) => usePriceFlash(price), {
      initialProps: { price: 100 },
    });

    rerender({ price: 100 });

    expect(result.current.direction).toBeNull();
  });

  it("fades: the direction clears once the animation window passes", () => {
    const { result, rerender } = renderHook(({ price }) => usePriceFlash(price), {
      initialProps: { price: 100 },
    });

    rerender({ price: 101 });
    expect(result.current.direction).toBe("up");

    act(() => {
      vi.advanceTimersByTime(FLASH_MS - 1);
    });
    expect(result.current.direction).toBe("up");

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(result.current.direction).toBeNull();
  });

  it("restarts on a second move inside the window, via a new key", () => {
    const { result, rerender } = renderHook(({ price }) => usePriceFlash(price), {
      initialProps: { price: 100 },
    });

    rerender({ price: 101 });
    const first = result.current.seq;

    act(() => {
      vi.advanceTimersByTime(FLASH_MS / 2);
    });
    rerender({ price: 102 });

    expect(result.current.direction).toBe("up");
    expect(result.current.seq).toBe(first + 1);

    // The earlier timer must not clear the newer flash early.
    act(() => {
      vi.advanceTimersByTime(FLASH_MS / 2);
    });
    expect(result.current.direction).toBe("up");
  });

  it("clears the flash when the price becomes unknown", () => {
    // A price that goes away is a dash, not a fall. Left set, the class would
    // stay on the cell for the rest of the session — invisible today only
    // because the animation has no fill-mode, and a trap for any style keyed
    // off it later.
    const { result, rerender } = renderHook(({ price }) => usePriceFlash(price), {
      initialProps: { price: 100 as number | null },
    });

    rerender({ price: 101 });
    expect(result.current.direction).toBe("up");

    rerender({ price: null });
    expect(result.current.direction).toBeNull();
  });

  it("clears its timer on unmount", () => {
    const { rerender, unmount } = renderHook(({ price }) => usePriceFlash(price), {
      initialProps: { price: 100 },
    });
    rerender({ price: 101 });

    unmount();

    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("flashClass", () => {
  it("maps a direction to its animation class", () => {
    expect(flashClass({ direction: "up", seq: 1 })).toBe("flash-up");
    expect(flashClass({ direction: "down", seq: 1 })).toBe("flash-down");
    expect(flashClass({ direction: null, seq: 1 })).toBe("");
  });
});
