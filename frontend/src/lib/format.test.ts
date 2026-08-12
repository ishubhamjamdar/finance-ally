import { describe, expect, it } from "vitest";

import {
  EM_DASH,
  formatClock,
  formatDollars,
  formatDollarsCompact,
  formatMoney,
  formatPercent,
  formatSigned,
  toneClass,
} from "@/lib/format";

describe("format", () => {
  it("renders an em dash for every flavour of unknown", () => {
    for (const format of [formatMoney, formatDollars, formatDollarsCompact, formatPercent]) {
      expect(format(null)).toBe(EM_DASH);
      expect(format(undefined)).toBe(EM_DASH);
      expect(format(Number.NaN)).toBe(EM_DASH);
      expect(format(Number.POSITIVE_INFINITY)).toBe(EM_DASH);
    }
  });

  it("never renders an unknown as zero", () => {
    // The backend goes to some trouble to send null rather than 0 for a price
    // it does not have. Rendering it as 0.00 would throw that away.
    expect(formatMoney(null)).not.toBe("0.00");
    expect(formatDollars(null)).not.toBe("$0.00");
    expect(formatPercent(null)).not.toBe("+0.00%");
  });

  it("groups and fixes money to cents", () => {
    expect(formatMoney(1234.5)).toBe("1,234.50");
    expect(formatDollars(1234.5)).toBe("$1,234.50");
    expect(formatDollars(0)).toBe("$0.00");
  });

  it("drops the cents for the header total", () => {
    expect(formatDollarsCompact(12345.67)).toBe("$12,346");
    expect(formatDollarsCompact(10000)).toBe("$10,000");
  });

  it("always signs a change", () => {
    expect(formatPercent(1.234)).toBe("+1.23%");
    expect(formatPercent(-1.235)).toBe("-1.24%");
    expect(formatPercent(0)).toBe("+0.00%");
    expect(formatSigned(-1.2)).toBe("-1.20");
    expect(formatSigned(1.2)).toBe("+1.20");
  });

  it("tones by sign, and mutes an unknown rather than calling it flat", () => {
    expect(toneClass(1)).toBe("text-up");
    expect(toneClass(-1)).toBe("text-down");
    expect(toneClass(0)).toBe("text-muted");
    expect(toneClass(null)).toBe("text-faint");
  });

  it("reads timestamps as epoch seconds, not milliseconds", () => {
    // Pinned by arithmetic rather than by a literal, so the assertion holds in
    // any time zone. Adding 3600 must move the clock a whole hour; a function
    // that read the input as milliseconds would move it 3.6 seconds.
    //
    // The obvious version of this test — compare against `new Date(t)` and
    // assert they differ — cannot fail: 1_760_000_000 seconds and
    // 1_760_000_000 milliseconds are exactly 20,350 days apart and render the
    // same time of day.
    const seconds = 1_760_000_000;
    const [hour, minute, second] = formatClock(seconds).split(":").map(Number);
    const [laterHour, laterMinute, laterSecond] = formatClock(seconds + 3600).split(":").map(Number);

    expect(laterHour).toBe((hour + 1) % 24);
    expect(laterMinute).toBe(minute);
    expect(laterSecond).toBe(second);
    expect(formatClock(null)).toBe(EM_DASH);
  });
});
