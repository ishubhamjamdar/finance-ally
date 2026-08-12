"use client";

/**
 * The workstation's shared state, owned in one place.
 *
 * ## Why this exists rather than props
 *
 * There must be exactly one `EventSource` per page. `usePriceStream` opens one
 * per call, so a component that reaches for it directly — the main chart, a
 * heatmap, anything that wants a price — silently doubles the number of
 * streams the backend is feeding. Nothing about the hook's signature stops
 * that; what stops it is that this provider is the only caller, and every
 * component gets prices from `useMarket()` instead.
 *
 * The account half is here for the same reason in reverse: a trade from
 * Checkpoint 6's trade bar, and an auto-executed trade from Checkpoint 7's
 * chat, both have to make the header, the positions table and the heatmap
 * agree again. `refresh()` is that one call, so no panel has to re-fetch the
 * portfolio on its own and no two panels can disagree about which fetch won.
 *
 * The two contexts are separate deliberately. The market value changes twice a
 * second; the account value changes when the user trades. Merging them would
 * re-render every account consumer on every tick.
 */

import { createContext, useCallback, useContext, useMemo } from "react";
import type { ReactNode } from "react";

import { useApiResource } from "@/hooks/useApiResource";
import { usePriceStream } from "@/hooks/usePriceStream";
import type { PriceStream } from "@/hooks/usePriceStream";
import { ENDPOINTS } from "@/lib/api";
import type { Portfolio, WatchlistResponse, WatchlistRow } from "@/lib/types";

export interface Account {
  /** Null until the first fetch lands, and after a failure. Never a fake zero. */
  portfolio: Portfolio | null;
  /** In add order, as the endpoint returned it. */
  watchlist: WatchlistRow[];
  loading: boolean;
  /** The first error either fetch reported, with the backend's own wording. */
  error: string | null;
  /** Re-read both after something changed the account. */
  refresh: () => void;
}

const MarketContext = createContext<PriceStream | null>(null);
const AccountContext = createContext<Account | null>(null);

export function TerminalProvider({ children }: { children: ReactNode }) {
  const market = usePriceStream(ENDPOINTS.priceStream);
  const portfolio = useApiResource<Portfolio>(ENDPOINTS.portfolio);
  const watchlist = useApiResource<WatchlistResponse>(ENDPOINTS.watchlist);

  const { reload: reloadPortfolio } = portfolio;
  const { reload: reloadWatchlist } = watchlist;
  const refresh = useCallback(() => {
    reloadPortfolio();
    reloadWatchlist();
  }, [reloadPortfolio, reloadWatchlist]);

  const account = useMemo<Account>(
    () => ({
      portfolio: portfolio.data,
      watchlist: watchlist.data?.tickers ?? [],
      loading: portfolio.loading || watchlist.loading,
      error: portfolio.error ?? watchlist.error,
      refresh,
    }),
    [
      portfolio.data,
      portfolio.loading,
      portfolio.error,
      watchlist.data,
      watchlist.loading,
      watchlist.error,
      refresh,
    ],
  );

  return (
    <MarketContext.Provider value={market}>
      <AccountContext.Provider value={account}>{children}</AccountContext.Provider>
    </MarketContext.Provider>
  );
}

/** Live prices, sparklines, shocks and the connection status. */
export function useMarket(): PriceStream {
  const market = useContext(MarketContext);
  if (market === null) throw new Error("useMarket must be used inside <TerminalProvider>");
  return market;
}

/** Cash, positions and the watchlist, with one way to re-read them. */
export function useAccount(): Account {
  const account = useContext(AccountContext);
  if (account === null) throw new Error("useAccount must be used inside <TerminalProvider>");
  return account;
}
