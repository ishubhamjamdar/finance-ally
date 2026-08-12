import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  MAX_SHOCKS,
  MAX_SPARKLINE_POINTS,
  RECONNECT_GRACE_MS,
  usePriceStream,
} from "@/hooks/usePriceStream";
import { FakeEventSource } from "@/test/FakeEventSource";
import { makeFrame, makeQuote } from "@/test/fixtures";

beforeEach(() => {
  FakeEventSource.install();
});

afterEach(() => {
  FakeEventSource.reset();
});

describe("usePriceStream", () => {
  it("opens one stream at the configured path", () => {
    renderHook(() => usePriceStream("/api/stream/prices"));

    expect(FakeEventSource.only.url).toBe("/api/stream/prices");
  });

  it("stores the quotes from a price frame", () => {
    const { result } = renderHook(() => usePriceStream());

    act(() => {
      FakeEventSource.only.emitOpen();
      FakeEventSource.only.emitMessage(makeFrame({ AAPL: 190.5, MSFT: 420.25 }));
    });

    expect(result.current.prices.AAPL.price).toBe(190.5);
    expect(result.current.prices.MSFT.price).toBe(420.25);
    expect(result.current.frames).toBe(1);
    expect(result.current.lastFrameAt).not.toBeNull();
  });

  it("accumulates a sparkline point per frame, from empty", () => {
    const { result } = renderHook(() => usePriceStream());

    expect(result.current.sparklines.AAPL).toBeUndefined();

    act(() => {
      FakeEventSource.only.emitMessage(makeFrame({ AAPL: 100 }));
    });
    act(() => {
      FakeEventSource.only.emitMessage(makeFrame({ AAPL: 101 }));
    });
    act(() => {
      FakeEventSource.only.emitMessage(makeFrame({ AAPL: 99 }));
    });

    expect(result.current.sparklines.AAPL).toEqual([100, 101, 99]);
  });

  it("caps the sparkline window, dropping the oldest points", () => {
    const { result } = renderHook(() => usePriceStream());

    act(() => {
      for (let index = 0; index < MAX_SPARKLINE_POINTS + 10; index += 1) {
        FakeEventSource.only.emitMessage(makeFrame({ AAPL: index }));
      }
    });

    const series = result.current.sparklines.AAPL;
    expect(series).toHaveLength(MAX_SPARKLINE_POINTS);
    expect(series[0]).toBe(10);
    expect(series[series.length - 1]).toBe(MAX_SPARKLINE_POINTS + 9);
  });

  it("keeps a ticker's series when a later frame omits it", () => {
    const { result } = renderHook(() => usePriceStream());

    act(() => {
      FakeEventSource.only.emitMessage(makeFrame({ AAPL: 100, MSFT: 400 }));
    });
    act(() => {
      FakeEventSource.only.emitMessage(makeFrame({ AAPL: 101 }));
    });

    expect(result.current.sparklines.AAPL).toEqual([100, 101]);
    expect(result.current.sparklines.MSFT).toEqual([400]);
    expect(result.current.prices.MSFT.price).toBe(400);
  });

  it("ignores a malformed frame rather than poisoning the series", () => {
    const { result } = renderHook(() => usePriceStream());

    act(() => {
      FakeEventSource.only.emitMessage(makeFrame({ AAPL: 100 }));
    });
    act(() => {
      FakeEventSource.only.emitMessage("{not json");
      FakeEventSource.only.emitMessage("[1,2,3]");
      FakeEventSource.only.emitMessage({ AAPL: { ticker: "AAPL", price: null } });
    });

    expect(result.current.sparklines.AAPL).toEqual([100]);
    expect(result.current.prices.AAPL.price).toBe(100);
  });

  it("does not count a frame that stored nothing", () => {
    // A climbing frame count beside a grid full of dashes reports "the market
    // is quiet" for what is actually a broken feed.
    const { result } = renderHook(() => usePriceStream());

    act(() => {
      FakeEventSource.only.emitMessage(makeFrame({ AAPL: 100 }));
    });
    const { frames, lastFrameAt } = result.current;

    act(() => {
      FakeEventSource.only.emitMessage({ AAPL: { ticker: "AAPL", price: null } });
      FakeEventSource.only.emitMessage("{not json");
    });

    expect(result.current.frames).toBe(frames);
    expect(result.current.lastFrameAt).toBe(lastFrameAt);
  });

  it("collects shock events newest first, bounded", () => {
    const { result } = renderHook(() => usePriceStream());

    act(() => {
      for (let index = 0; index < MAX_SHOCKS + 3; index += 1) {
        FakeEventSource.only.emitNamed("shock", {
          ticker: "TSLA",
          magnitude_percent: -3.4,
          price: 240 + index,
          timestamp: 1_760_000_000 + index,
        });
      }
    });

    expect(result.current.shocks).toHaveLength(MAX_SHOCKS);
    expect(result.current.shocks[0].price).toBe(240 + MAX_SHOCKS + 2);
  });

  describe("connection status", () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it("starts connecting and turns connected on open", () => {
      const { result } = renderHook(() => usePriceStream());
      expect(result.current.status).toBe("connecting");

      act(() => {
        FakeEventSource.only.emitOpen();
      });

      expect(result.current.status).toBe("connected");
    });

    it("goes reconnecting on an error the browser will retry", () => {
      const { result } = renderHook(() => usePriceStream());

      act(() => {
        FakeEventSource.only.emitOpen();
        FakeEventSource.only.emitError();
      });

      expect(result.current.status).toBe("reconnecting");
    });

    it("escalates to disconnected once the grace period expires", () => {
      const { result } = renderHook(() => usePriceStream());

      act(() => {
        FakeEventSource.only.emitOpen();
        FakeEventSource.only.emitError();
      });
      act(() => {
        vi.advanceTimersByTime(RECONNECT_GRACE_MS - 1);
      });
      expect(result.current.status).toBe("reconnecting");

      act(() => {
        vi.advanceTimersByTime(1);
      });
      expect(result.current.status).toBe("disconnected");
    });

    it("recovers to connected without a remount, and re-arms the escalation", () => {
      const { result } = renderHook(() => usePriceStream());

      act(() => {
        FakeEventSource.only.emitOpen();
        FakeEventSource.only.emitError();
      });
      act(() => {
        vi.advanceTimersByTime(RECONNECT_GRACE_MS);
      });
      expect(result.current.status).toBe("disconnected");

      act(() => {
        FakeEventSource.only.emitOpen();
      });
      expect(result.current.status).toBe("connected");

      // A second outage must behave like the first: amber, then red.
      act(() => {
        FakeEventSource.only.emitError();
      });
      expect(result.current.status).toBe("reconnecting");
      act(() => {
        vi.advanceTimersByTime(RECONNECT_GRACE_MS);
      });
      expect(result.current.status).toBe("disconnected");
    });

    it("does not let a recovered blip fire a stale escalation", () => {
      const { result } = renderHook(() => usePriceStream());

      act(() => {
        FakeEventSource.only.emitOpen();
        FakeEventSource.only.emitError();
      });
      act(() => {
        vi.advanceTimersByTime(RECONNECT_GRACE_MS / 2);
        FakeEventSource.only.emitOpen();
      });
      act(() => {
        vi.advanceTimersByTime(RECONNECT_GRACE_MS);
      });

      expect(result.current.status).toBe("connected");
    });

    it("is disconnected immediately when the browser gives up", () => {
      const { result } = renderHook(() => usePriceStream());

      act(() => {
        FakeEventSource.only.emitOpen();
        FakeEventSource.only.emitError({ fatal: true });
      });

      expect(result.current.status).toBe("disconnected");
    });
  });

  describe("lifecycle", () => {
    it("never opens a second connection, however many times it drops", () => {
      renderHook(() => usePriceStream());

      act(() => {
        FakeEventSource.only.emitOpen();
        for (let index = 0; index < 5; index += 1) {
          FakeEventSource.only.emitError();
          FakeEventSource.only.emitOpen();
        }
      });

      // EventSource reconnects by itself. A hook that opened its own would
      // leave the server feeding six copies of the stream to one page.
      expect(FakeEventSource.instances).toHaveLength(1);
    });

    it("closes the stream and removes every listener on unmount", () => {
      const { unmount } = renderHook(() => usePriceStream());
      const source = FakeEventSource.only;

      expect(source.listenerCount()).toBeGreaterThan(0);

      unmount();

      expect(source.closeCalls).toBe(1);
      expect(source.listenerCount()).toBe(0);
      expect(source.readyState).toBe(FakeEventSource.CLOSED);
    });

    it("does not leave an escalation timer running after unmount", () => {
      vi.useFakeTimers();
      try {
        const { unmount } = renderHook(() => usePriceStream());
        act(() => {
          FakeEventSource.only.emitError();
        });

        unmount();

        expect(vi.getTimerCount()).toBe(0);
      } finally {
        vi.useRealTimers();
      }
    });

    it("opens a fresh stream when the path changes, closing the old one", () => {
      const { rerender } = renderHook(({ path }) => usePriceStream(path), {
        initialProps: { path: "/api/stream/prices" },
      });
      const first = FakeEventSource.instances[0];

      rerender({ path: "/api/stream/other" });

      expect(first.closeCalls).toBe(1);
      expect(FakeEventSource.instances).toHaveLength(2);
      expect(FakeEventSource.instances[1].url).toBe("/api/stream/other");
    });
  });

  it("prefers the newest quote for a ticker", () => {
    const { result } = renderHook(() => usePriceStream());

    act(() => {
      FakeEventSource.only.emitMessage({ AAPL: makeQuote("AAPL", 190) });
    });
    act(() => {
      FakeEventSource.only.emitMessage({ AAPL: makeQuote("AAPL", 191, { previous_price: 190 }) });
    });

    expect(result.current.prices.AAPL.price).toBe(191);
    expect(result.current.prices.AAPL.direction).toBe("up");
  });
});
