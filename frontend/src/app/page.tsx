"use client";

/**
 * The workstation.
 *
 * One `EventSource` for the whole page — every panel reads the same stream
 * state, so there is exactly one connection to the backend no matter how many
 * components render a price.
 *
 * Checkpoint 5 fills the header and the watchlist. The main chart, the
 * portfolio heatmap, the positions table and the trade bar are Checkpoint 6;
 * the chat sidebar is Checkpoint 7. The grid below is where they go.
 */

import { FeedPanel } from "@/components/FeedPanel";
import { Header } from "@/components/Header";
import { WatchlistPanel } from "@/components/WatchlistPanel";
import { useApiResource } from "@/hooks/useApiResource";
import { usePriceStream } from "@/hooks/usePriceStream";
import { ENDPOINTS } from "@/lib/api";
import type { Portfolio, WatchlistResponse } from "@/lib/types";

export default function Workstation() {
  const stream = usePriceStream(ENDPOINTS.priceStream);
  const watchlist = useApiResource<WatchlistResponse>(ENDPOINTS.watchlist);
  const portfolio = useApiResource<Portfolio>(ENDPOINTS.portfolio);

  return (
    <div className="flex h-dvh flex-col bg-terminal text-ink">
      <Header portfolio={portfolio.data} prices={stream.prices} status={stream.status} />

      <main className="grid min-h-0 flex-1 grid-cols-1 gap-2 p-2 lg:grid-cols-[minmax(320px,380px)_1fr]">
        <WatchlistPanel
          rows={watchlist.data?.tickers ?? []}
          prices={stream.prices}
          sparklines={stream.sparklines}
          loading={watchlist.loading}
          error={watchlist.error}
        />

        <FeedPanel
          status={stream.status}
          frames={stream.frames}
          lastFrameAt={stream.lastFrameAt}
          tickerCount={Object.keys(stream.prices).length}
          shocks={stream.shocks}
        />
      </main>
    </div>
  );
}
