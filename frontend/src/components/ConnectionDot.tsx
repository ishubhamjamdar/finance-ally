"use client";

/**
 * The feed indicator — PLAN.md §2: green connected, yellow reconnecting, red
 * disconnected.
 *
 * The dot carries a text label beside it rather than relying on colour alone,
 * and the accessible name says the same thing, so "is the feed up?" does not
 * depend on distinguishing amber from red.
 */

import type { ConnectionStatus } from "@/hooks/usePriceStream";

const APPEARANCE: Record<ConnectionStatus, { label: string; dot: string; text: string }> = {
  connecting: { label: "Connecting", dot: "bg-accent", text: "text-accent" },
  connected: { label: "Live", dot: "bg-up", text: "text-up" },
  reconnecting: { label: "Reconnecting", dot: "bg-accent", text: "text-accent" },
  disconnected: { label: "Disconnected", dot: "bg-down", text: "text-down" },
};

export function ConnectionDot({ status }: { status: ConnectionStatus }) {
  const { label, dot, text } = APPEARANCE[status];
  const pulsing = status === "connecting" || status === "reconnecting";

  return (
    <span
      className="flex items-center gap-2"
      role="status"
      aria-label={`Market feed: ${label}`}
      data-status={status}
    >
      <span
        className={`h-2 w-2 rounded-full ${dot} ${pulsing ? "animate-pulse" : ""}`}
        data-testid="connection-dot"
      />
      <span className={`text-[11px] font-medium tracking-wide uppercase ${text}`}>{label}</span>
    </span>
  );
}
