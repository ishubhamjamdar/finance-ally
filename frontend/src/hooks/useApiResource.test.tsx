import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

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
});
