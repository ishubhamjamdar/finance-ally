import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "FinAlly — AI Trading Workstation",
  description: "Live market data, a simulated portfolio, and an AI trading copilot.",
};

// Typed here rather than with Next's generated `LayoutProps` helper: that type
// only exists once `.next/types` has been written, so `npm run typecheck` on a
// clean checkout — which is what CI does — would fail to find it.
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full bg-terminal text-ink">{children}</body>
    </html>
  );
}
