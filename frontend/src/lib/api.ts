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
  watchlist: "/api/watchlist",
  priceStream: "/api/stream/prices",
} as const;

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

async function readDetail(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (body && typeof body === "object" && typeof body.detail === "string") {
      return body.detail;
    }
  } catch {
    // A non-JSON error body is normal for a proxy or a crash. Fall through.
  }
  return `${response.status} ${response.statusText}`.trim();
}
