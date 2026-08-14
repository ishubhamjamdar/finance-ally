/**
 * What a ticker symbol is, on the client.
 *
 * The authority is `backend/app/market/models.py::TICKER_PATTERN`, and this is
 * the other half of that contract — the same rule, so a symbol the server will
 * refuse is caught in the form rather than after a round trip.
 *
 * It is a *courtesy*, never a substitute. The server validates every symbol
 * again at the edge and again in `app.portfolio` / `app.watchlist`; nothing
 * here is load-bearing for correctness. What it buys is the error message: a
 * 422 from FastAPI arrives as a field-level validation report, and "PYPL!! is
 * not a ticker symbol" is what the person typing it can act on.
 */

/** Mirrors `TICKER_PATTERN`: a letter, then up to nine letters, digits, dots or dashes. */
export const TICKER_PATTERN = /^[A-Za-z][A-Za-z0-9.\-]{0,9}$/;

export function isTicker(value: string): boolean {
  return TICKER_PATTERN.test(value);
}

/** The backend upper-cases every symbol it stores; the UI should send what it will store. */
export function normalizeTicker(value: string): string {
  return value.trim().toUpperCase();
}
