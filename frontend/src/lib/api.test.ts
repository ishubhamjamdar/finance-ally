import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, describeError, sendJson, watchlistEntryPath } from "@/lib/api";

function stubResponse(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(
    async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json" },
      }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("sendJson", () => {
  it("posts a JSON body with the header the backend needs to parse it", async () => {
    const fetchMock = stubResponse({ ok: true }, 201);

    await sendJson("/api/portfolio/trade", "POST", { ticker: "AAPL", side: "buy", quantity: 2 });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect(init.headers).toEqual({ "Content-Type": "application/json" });
    expect(JSON.parse(init.body as string)).toEqual({
      ticker: "AAPL",
      side: "buy",
      quantity: 2,
    });
  });

  it("sends a DELETE with no body and no content type", async () => {
    // A DELETE carrying `Content-Type: application/json` and no body is the
    // kind of request a strict server is entitled to reject.
    const fetchMock = stubResponse({ removed: true });

    await sendJson(watchlistEntryPath("AAPL"), "DELETE");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/watchlist/AAPL");
    expect(init.method).toBe("DELETE");
    expect(init.body).toBeUndefined();
    expect(init.headers).toBeUndefined();
  });

  it("returns the parsed body on success", async () => {
    stubResponse({ trade: { ticker: "AAPL", price: 190.5 } }, 201);

    const result = await sendJson<{ trade: { ticker: string; price: number } }>(
      "/api/portfolio/trade",
      "POST",
      {},
    );

    expect(result.trade).toEqual({ ticker: "AAPL", price: 190.5 });
  });

  it("carries the backend's own reason and its status code on a rejection", async () => {
    // The whole point. PLAN.md §8 makes 400 "the account cannot support this"
    // and 422 "the request was malformed", and the trade bar has to show which
    // — "request failed" is the one thing the user cannot act on.
    stubResponse({ detail: "Insufficient cash: need $19,050.00, have $10,000.00" }, 400);

    await expect(sendJson("/api/portfolio/trade", "POST", {})).rejects.toMatchObject({
      name: "ApiError",
      status: 400,
      message: "Insufficient cash: need $19,050.00, have $10,000.00",
    });
  });

  it("falls back to the status line when the error body is not JSON", async () => {
    const fetchMock = vi.fn(async () => new Response("<html>502</html>", { status: 502 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(sendJson("/api/watchlist", "POST", {})).rejects.toMatchObject({ status: 502 });
  });
});

describe("watchlistEntryPath", () => {
  it("addresses the entry the backend's DELETE route expects", () => {
    expect(watchlistEntryPath("AAPL")).toBe("/api/watchlist/AAPL");
  });

  it("encodes a symbol that would otherwise change the path", () => {
    // `TICKER_PATTERN` forbids a slash, so this is unreachable through the
    // form — but a path built by concatenation is how it stops being.
    expect(watchlistEntryPath("A/B")).toBe("/api/watchlist/A%2FB");
  });
});

describe("describeError", () => {
  it("keeps the backend's wording", () => {
    expect(describeError(new ApiError("Ticker already watched", 409))).toBe(
      "Ticker already watched",
    );
  });

  it("says the server is unreachable rather than 'Failed to fetch'", () => {
    expect(describeError(new TypeError("Failed to fetch"))).toBe("Cannot reach the server");
  });

  it("handles something that is not an Error at all", () => {
    expect(describeError("boom")).toBe("boom");
  });
});
