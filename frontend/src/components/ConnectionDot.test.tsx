import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ConnectionDot } from "@/components/ConnectionDot";

describe("ConnectionDot", () => {
  it("names each connection state, not just colours it", () => {
    const { rerender } = render(<ConnectionDot status="connecting" />);
    expect(screen.getByRole("status")).toHaveAccessibleName("Market feed: Connecting");

    rerender(<ConnectionDot status="connected" />);
    expect(screen.getByRole("status")).toHaveAccessibleName("Market feed: Live");
  });

  it("says Stalled when the connection is fine and nothing is arriving", () => {
    // Reporting this as Live is the specific dishonesty it exists to prevent:
    // an open socket over a wedged feed, with frozen prices on screen.
    render(<ConnectionDot status="connected" stalled={true} />);

    expect(screen.getByRole("status")).toHaveAccessibleName("Market feed: Stalled");
    expect(screen.getByTestId("connection-dot").className).toContain("bg-accent");
    expect(screen.getByRole("status")).toHaveAttribute("data-status", "stalled");
  });

  it("lets a real disconnection win over a stall", () => {
    // Both can be true at once — the feed stops, then the socket drops. Red is
    // the more urgent of the two and must not be masked by amber.
    render(<ConnectionDot status="disconnected" stalled={true} />);

    expect(screen.getByRole("status")).toHaveAccessibleName("Market feed: Disconnected");
    expect(screen.getByTestId("connection-dot").className).toContain("bg-down");
  });
});
