import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useApiResource } from "@/hooks/useApiResource";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function stubFetch(implementation: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>) {
  const fetchMock = vi.fn(implementation);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useApiResource", () => {
  it("fetches once on mount and reports the body", async () => {
    const fetchMock = stubFetch(async () => jsonResponse({ cash_balance: 10000 }));

    const { result } = renderHook(() => useApiResource<{ cash_balance: number }>("/api/portfolio"));

    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.data).toEqual({ cash_balance: 10000 });
    expect(result.current.error).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/portfolio");
  });

  it("keeps the backend's own reason for a failure", async () => {
    // PLAN.md §8 gives every failure a detail worth reading; "request failed"
    // would throw away the only part the user can act on.
    stubFetch(async () => jsonResponse({ detail: "No market data source is running" }, 503));

    const { result } = renderHook(() => useApiResource("/api/watchlist"));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("No market data source is running");
    expect(result.current.data).toBeNull();
  });

  it("says the server is unreachable when fetch itself fails", async () => {
    stubFetch(async () => {
      throw new TypeError("Failed to fetch");
    });

    const { result } = renderHook(() => useApiResource("/api/watchlist"));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("Cannot reach the server");
  });

  it("survives a non-JSON error body", async () => {
    stubFetch(async () => new Response("<html>502</html>", { status: 502, statusText: "Bad Gateway" }));

    const { result } = renderHook(() => useApiResource("/api/watchlist"));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toContain("502");
  });

  it("re-fetches on reload and clears a previous error", async () => {
    let attempt = 0;
    const fetchMock = stubFetch(async () => {
      attempt += 1;
      return attempt === 1 ? jsonResponse({ detail: "boom" }, 500) : jsonResponse({ ok: true });
    });

    const { result } = renderHook(() => useApiResource<{ ok: boolean }>("/api/watchlist"));
    await waitFor(() => expect(result.current.error).toBe("boom"));

    act(() => result.current.reload());

    await waitFor(() => expect(result.current.data).toEqual({ ok: true }));
    expect(result.current.error).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("aborts the request on unmount rather than setting state afterwards", async () => {
    let signal: AbortSignal | undefined;
    stubFetch(async (_input, init) => {
      signal = init?.signal ?? undefined;
      return new Promise<Response>(() => {
        // never resolves; the abort is the only way out
      });
    });

    const { unmount } = renderHook(() => useApiResource("/api/watchlist"));
    await waitFor(() => expect(signal).toBeDefined());

    unmount();

    expect(signal?.aborted).toBe(true);
  });
  describe("refreshMs", () => {
    // Fake timers from before the mount, not after it: an interval created
    // under the real clock is not one `advanceTimersByTime` can fire, and a
    // test that switches over halfway passes whether the hook polls or not.
    //
    // `waitFor` is unusable in here for the same reason in reverse — it polls
    // on a `setInterval` this has just frozen — so settling is done by
    // flushing the microtask queue inside `act`, which is what the fetch
    // stub's promise is waiting on anyway.
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    const settle = () => act(async () => {});

    it("does not poll at all unless asked to", async () => {
      // The default, and the reason this hook exists in the shape it does:
      // everything that changes twice a second arrives on the SSE stream.
      const fetchMock = stubFetch(async () => jsonResponse({ ok: true }));

      const { result } = renderHook(() => useApiResource("/api/portfolio"));
      await settle();
      expect(result.current.data).toEqual({ ok: true });

      await act(async () => {
        vi.advanceTimersByTime(120_000);
      });

      expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    it("re-reads on the interval, for a series that grows on the server's clock", async () => {
      // `portfolio_snapshots` gains a row every 30 seconds with no user action
      // and no stream frame to announce it.
      const fetchMock = stubFetch(async () => jsonResponse({ snapshots: [] }));

      const { result } = renderHook(() => useApiResource("/api/portfolio/history", 30_000));
      await settle();
      expect(result.current.data).toEqual({ snapshots: [] });
      expect(fetchMock).toHaveBeenCalledTimes(1);

      await act(async () => {
        vi.advanceTimersByTime(30_000);
      });
      expect(fetchMock).toHaveBeenCalledTimes(2);

      await act(async () => {
        vi.advanceTimersByTime(30_000);
      });
      expect(fetchMock).toHaveBeenCalledTimes(3);
    });

    it("stops polling when the component unmounts", async () => {
      // An interval that outlives its mount keeps a dead component fetching
      // for the life of the page.
      const fetchMock = stubFetch(async () => jsonResponse({ snapshots: [] }));

      const { unmount } = renderHook(() => useApiResource("/api/portfolio/history", 1000));
      await settle();

      unmount();
      await act(async () => {
        vi.advanceTimersByTime(10_000);
      });

      expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    it("keeps the last series through a failed poll rather than blanking it", async () => {
      let attempt = 0;
      stubFetch(async () => {
        attempt += 1;
        return attempt === 1
          ? jsonResponse({ snapshots: [{ total_value: 10000, recorded_at: "x" }] })
          : jsonResponse({ detail: "gone" }, 503);
      });

      const { result } = renderHook(() =>
        useApiResource<{ snapshots: unknown[] }>("/api/portfolio/history", 1000),
      );
      await settle();
      expect(result.current.data?.snapshots).toHaveLength(1);

      await act(async () => {
        vi.advanceTimersByTime(1000);
      });
      await settle();

      expect(result.current.error).toBe("gone");
      expect(result.current.data?.snapshots).toHaveLength(1);
    });
  });
});
