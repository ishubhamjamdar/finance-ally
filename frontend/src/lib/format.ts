/**
 * Display formatting. One owner, because a price rendered to two decimals in
 * the watchlist and four in the header is the kind of drift this codebase
 * keeps eliminating.
 *
 * Every function takes `null | undefined` and returns an em dash for it.
 * `backend/app/api/watchlist.py` says it outright: a ticker the source has not
 * priced yet renders as a dash, and the frontend must not substitute zero.
 */

/** What every "unknown" renders as. Not "0.00", not "-", not blank. */
export const EM_DASH = "—";

const MONEY = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const WHOLE_MONEY = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 0,
});

/** `1234.5` → `"1,234.50"`. No currency symbol; the label supplies it. */
export function formatMoney(value: number | null | undefined): string {
  if (!Number.isFinite(value ?? NaN)) return EM_DASH;
  return MONEY.format(value as number);
}

/** `1234.5` → `"$1,234.50"`. */
export function formatDollars(value: number | null | undefined): string {
  if (!Number.isFinite(value ?? NaN)) return EM_DASH;
  return `$${MONEY.format(value as number)}`;
}

/**
 * `12345.6` → `"$12,346"`. For the header total, where the cents change twice a
 * second and carry no information the eye can use.
 */
export function formatDollarsCompact(value: number | null | undefined): string {
  if (!Number.isFinite(value ?? NaN)) return EM_DASH;
  return `$${WHOLE_MONEY.format(value as number)}`;
}

/** `-1.234` → `"-1.23%"`, `1.234` → `"+1.23%"`. Sign always shown. */
export function formatPercent(value: number | null | undefined, digits = 2): string {
  if (!Number.isFinite(value ?? NaN)) return EM_DASH;
  const number = value as number;
  return `${number >= 0 ? "+" : ""}${number.toFixed(digits)}%`;
}

/**
 * `-123.4` → `"-$123.40"`, `123.4` → `"+$123.40"`.
 *
 * The sign goes outside the symbol. `$-123.40` is what `formatDollars` of a
 * negative produces, and it reads as a price rather than a loss.
 */
export function formatSignedDollars(value: number | null | undefined): string {
  if (!Number.isFinite(value ?? NaN)) return EM_DASH;
  const number = value as number;
  return `${number < 0 ? "-" : "+"}$${MONEY.format(Math.abs(number))}`;
}

/**
 * Shares, which are fractional in this system. Trailing zeros are dropped so a
 * whole share reads `10` rather than `10.0000`, and four places survive
 * because that is the precision `positions.quantity` is stored at.
 */
export function formatQuantity(value: number | null | undefined): string {
  if (!Number.isFinite(value ?? NaN)) return EM_DASH;
  return String(Number((value as number).toFixed(4)));
}

/** `-1.234` → `"-1.23"`, `1.234` → `"+1.23"`. */
export function formatSigned(value: number | null | undefined, digits = 2): string {
  if (!Number.isFinite(value ?? NaN)) return EM_DASH;
  const number = value as number;
  return `${number >= 0 ? "+" : ""}${number.toFixed(digits)}`;
}

/**
 * The Tailwind text colour for a signed number: green up, red down, muted flat.
 *
 * `null` is muted rather than green — an unknown change is not a zero change.
 */
export function toneClass(value: number | null | undefined): string {
  if (!Number.isFinite(value ?? NaN)) return "text-faint";
  const number = value as number;
  if (number > 0) return "text-up";
  if (number < 0) return "text-down";
  return "text-muted";
}

/**
 * Epoch **seconds** (what the backend sends) → a local wall-clock time.
 * The stream's timestamps come from Python's `time.time()`; multiplying by
 * 1000 in each call site is how one of them eventually forgets to.
 */
export function formatClock(epochSeconds: number | null | undefined): string {
  if (!Number.isFinite(epochSeconds ?? NaN)) return EM_DASH;
  return new Date((epochSeconds as number) * 1000).toLocaleTimeString("en-US", {
    hour12: false,
  });
}
