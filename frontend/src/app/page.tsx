"use client";

/**
 * The workstation.
 *
 * Checkpoint 5 fills the header and the watchlist. The main chart, the
 * portfolio heatmap, the positions table and the trade bar are Checkpoint 6;
 * the chat sidebar is Checkpoint 7. They go in the grid below, and they read
 * prices from `useMarket()` — never by calling `usePriceStream` again, which
 * would open a second connection to the same feed.
 */

import { FeedPanel } from "@/components/FeedPanel";
import { Header } from "@/components/Header";
import { WatchlistPanel } from "@/components/WatchlistPanel";
import { TerminalProvider, useAccount, useMarket } from "@/state/TerminalProvider";

export default function Page() {
  return (
    <TerminalProvider>
      <Workstation />
    </TerminalProvider>
  );
}

function Workstation() {
  const market = useMarket();
  const account = useAccount();

  return (
    <div className="flex h-dvh flex-col bg-terminal text-ink">
      <Header portfolio={account.portfolio} prices={market.prices} status={market.status} />

      <main className="grid min-h-0 flex-1 grid-cols-1 gap-2 p-2 lg:grid-cols-[minmax(320px,380px)_1fr]">
        <WatchlistPanel
          rows={account.watchlist}
          prices={market.prices}
          sparklines={market.sparklines}
          loading={account.loading}
          error={account.error}
        />

        <FeedPanel
          status={market.status}
          frames={market.frames}
          lastFrameAt={market.lastFrameAt}
          tickerCount={Object.keys(market.prices).length}
          shocks={market.shocks}
        />
      </main>
    </div>
  );
}
