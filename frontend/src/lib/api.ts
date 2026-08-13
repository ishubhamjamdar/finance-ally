/**
 * Where the API lives, and the one place that decides it.
 *
 * In production the export is served by the same FastAPI process that serves
 * `/api/*`, so a bare path is same-origin and there is no CORS configuration
 * anywhere in this project (PLAN.md §10).
 *
 * `next dev` is the exception: it serves the UI on :3000 while the API is on
 * :8000. Setting `NEXT_PUBLIC_API_BASE=http://localhost:8000` bridges that.
 * `output: 'export'` ignores `rewrites`, so a dev proxy is not an option.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

/**
 * The paths this frontend calls, written down once — PLAN.md §8. Endpoints
 * exist that are not listed here; they are added as the checkpoint that
 * consumes them lands, rather than sitting as a map of things nobody calls.
 */
export const ENDPOINTS = {
  portfolio: "/api/portfolio",
  portfolioHistory: "/api/portfolio/history",
  trade: "/api/portfolio/trade",
  watchlist: "/api/watchlist",
  priceStream: "/api/stream/prices",
} as const;

/** `DELETE /api/watchlist/{ticker}`. Encoded, though the pattern forbids anything to encode. */
export function watchlistEntryPath(ticker: string): string {
  return `${ENDPOINTS.watchlist}/${encodeURIComponent(ticker)}`;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * GET a JSON endpoint, turning a non-2xx into an `ApiError` carrying the
 * backend's own `detail` where it sent one.
 *
 * PLAN.md §8 gives every endpoint a documented failure code and a reason to go
 * with it — 400 "Insufficient cash", 503 "no market data source". Discarding
 * that in favour of "request failed" would throw away the only part of the
 * response the user can act on.
 */
export async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), init);

  if (!response.ok) {
    throw new ApiError(await readDetail(response), response.status);
  }

  return (await response.json()) as T;
}

/**
 * A mutation: `POST` with a JSON body, or `DELETE` without one.
 *
 * PLAN.md §8 gives every mutation a 400 the account could not support and a
 * 422 that was malformed. Both reach the user as the backend's own words —
 * `readDetail` understands each shape — and the code travels alongside on
 * `ApiError.status` for a caller that needs to tell them apart. Nothing in
 * Checkpoint 6 does: the forms reject a malformed symbol or quantity before
 * sending, so a 422 arriving here means something the UI did not anticipate,
 * and its field report is more use than a category would be.
 */
export async function sendJson<T>(
  path: string,
  method: "POST" | "DELETE",
  body?: unknown,
): Promise<T> {
  const response = await fetch(apiUrl(path), {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (!response.ok) {
    throw new ApiError(await readDetail(response), response.status);
  }

  return (await response.json()) as T;
}

/**
 * What went wrong, in words a user can act on.
 *
 * `fetch` rejects with a bare `TypeError` when the server is not there at all,
 * and "Failed to fetch" tells nobody anything. Everything else already carries
 * the backend's own `detail`, which is the half worth keeping.
 */
export function describeError(cause: unknown): string {
  if (cause instanceof ApiError) return cause.message;
  if (cause instanceof TypeError) return "Cannot reach the server";
  return cause instanceof Error ? cause.message : String(cause);
}

async function readDetail(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (body && typeof body === "object") {
      if (typeof body.detail === "string") return body.detail;
      // FastAPI serialises a 422 as an *array* of field reports, not a string.
      // Reading only the string form left every validation failure rendering
      // as "422 Unprocessable Content" — the one message with nothing in it
      // for the person who typed the value being complained about.
      if (Array.isArray(body.detail)) {
        const reported = (body.detail as unknown[])
          .map(describeValidationError)
          .filter((line) => line !== "");
        if (reported.length > 0) return reported.join("; ");
      }
    }
  } catch {
    // A non-JSON error body is normal for a proxy or a crash. Fall through.
  }
  return `${response.status} ${response.statusText}`.trim();
}

/** One entry of FastAPI's `detail` array: `{loc: ["body", "ticker"], msg: "..."}`. */
function describeValidationError(entry: unknown): string {
  if (entry === null || typeof entry !== "object") return "";
  const { loc, msg } = entry as { loc?: unknown; msg?: unknown };
  if (typeof msg !== "string") return "";

  // `loc` opens with the request part — "body", "query", "path" — which names
  // a place in the protocol rather than a place in the form.
  const field = Array.isArray(loc) ? loc.slice(1).join(".") : "";
  return field === "" ? msg : `${field}: ${msg}`;
}
