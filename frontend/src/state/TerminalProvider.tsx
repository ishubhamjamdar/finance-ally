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

import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { useApiResource } from "@/hooks/useApiResource";
import { usePriceStream } from "@/hooks/usePriceStream";
import type { PriceStream } from "@/hooks/usePriceStream";
import { ENDPOINTS, sendJson, watchlistEntryPath } from "@/lib/api";
import type {
  ChatHistoryResponse,
  ChatMessage,
  ChatReply,
  HistoryResponse,
  Portfolio,
  Snapshot,
  TradeFill,
  TradeOrder,
  TradeResponse,
  WatchlistResponse,
  WatchlistRemoval,
  WatchlistRow,
} from "@/lib/types";

/**
 * How often the snapshot series is re-read.
 *
 * It matches `SNAPSHOT_INTERVAL_SECONDS` in `backend/app/main.py`, because
 * that task is the only thing that grows the series without the user doing
 * anything. Polling faster would mostly re-fetch an identical list; slower and
 * the P&L chart would visibly lag the value in the header. A trade does not
 * wait for this — it writes a snapshot immediately and calls `refresh()`.
 */
export const HISTORY_REFRESH_MS = 30_000;

export interface Account {
  /** Null until the first fetch lands, and after a failure. Never a fake zero. */
  portfolio: Portfolio | null;
  /** In add order, as the endpoint returned it. */
  watchlist: WatchlistRow[];
  /** The `portfolio_snapshots` series, oldest first. */
  history: Snapshot[];
  /** The conversation, oldest first: what was stored, plus this session's turns. */
  chat: ChatMessage[];

  /**
   * One loading flag and one error per resource, never merged.
   *
   * They are read by different panels, and a merged pair puts one panel's
   * failure over another's perfectly current data: `GET /api/portfolio`
   * failing while the watchlist succeeds would otherwise paint "cannot reach
   * the server" across a grid of live, streaming prices — or, on a first load,
   * replace it entirely. The history poll makes this routine rather than
   * theoretical: it runs every 30 seconds, unattended.
   */
  portfolioLoading: boolean;
  portfolioError: string | null;
  watchlistLoading: boolean;
  watchlistError: string | null;
  historyLoading: boolean;
  historyError: string | null;
  chatLoading: boolean;
  chatError: string | null;
  /** Re-read everything after something changed the account. */
  refresh: () => void;

  /**
   * The account mutations, each one re-reading afterwards.
   *
   * They live here rather than in the components that trigger them because
   * what has to happen after a trade is not local to the trade bar: the
   * header, the positions table, the heatmap and the P&L chart all have to
   * agree again, and `refresh()` is the one call that makes them. Checkpoint
   * 7's chat executes the same actions through the LLM and needs the same
   * thing to happen afterwards.
   *
   * Each rejects with the backend's own reason rather than swallowing it — the
   * caller renders it. A failed mutation does not refresh: nothing changed.
   */
  trade: (order: TradeOrder) => Promise<TradeFill>;
  addTicker: (ticker: string) => Promise<void>;
  removeTicker: (ticker: string) => Promise<void>;

  /**
   * One conversational turn. Resolves with the complete reply — PLAN.md §9 is
   * explicit that there is no token streaming — and rejects with the backend's
   * reason: 503 for no feed or no provider, 422 for a message the schema
   * refused.
   *
   * A reply that *executed* something also calls `refresh()`, so an
   * LLM-executed trade lands in the header, the positions table, the heatmap
   * and the P&L chart exactly as a manual one does.
   */
  sendChat: (message: string) => Promise<ChatReply>;
}

const MarketContext = createContext<PriceStream | null>(null);
const AccountContext = createContext<Account | null>(null);

export function TerminalProvider({ children }: { children: ReactNode }) {
  const market = usePriceStream(ENDPOINTS.priceStream);
  const portfolio = useApiResource<Portfolio>(ENDPOINTS.portfolio);
  const watchlist = useApiResource<WatchlistResponse>(ENDPOINTS.watchlist);
  const history = useApiResource<HistoryResponse>(ENDPOINTS.portfolioHistory, HISTORY_REFRESH_MS);
  const chatHistory = useApiResource<ChatHistoryResponse>(ENDPOINTS.chatHistory);

  // This session's turns, kept beside the stored ones rather than re-fetched.
  //
  // The reply carries exactly what the backend persisted, so appending it is
  // not a guess — and it avoids the window a re-fetch would open, where the
  // turn has been sent, the transcript has not come back, and the message the
  // user just typed is on screen nowhere.
  //
  // The consequence is a rule: **`refresh()` must never reload the chat
  // history**, or every appended turn would appear twice until the page
  // reloads. It reloads the account, which is what a turn can change.
  const [turns, setTurns] = useState<ChatMessage[]>([]);
  const turnCount = useRef(0);

  const { reload: reloadPortfolio } = portfolio;
  const { reload: reloadWatchlist } = watchlist;
  const { reload: reloadHistory } = history;
  const refresh = useCallback(() => {
    reloadPortfolio();
    reloadWatchlist();
    reloadHistory();
  }, [reloadPortfolio, reloadWatchlist, reloadHistory]);

  const trade = useCallback(
    async (order: TradeOrder): Promise<TradeFill> => {
      const result = await sendJson<TradeResponse>(ENDPOINTS.trade, "POST", order);
      refresh();
      return result.trade;
    },
    [refresh],
  );

  const addTicker = useCallback(
    async (ticker: string): Promise<void> => {
      await sendJson(ENDPOINTS.watchlist, "POST", { ticker });
      refresh();
    },
    [refresh],
  );

  const removeTicker = useCallback(
    async (ticker: string): Promise<void> => {
      // The portfolio is re-read too, not just the list: removing a ticker
      // held as a position leaves the position in place, and the panels must
      // keep showing it.
      await sendJson<WatchlistRemoval>(watchlistEntryPath(ticker), "DELETE");
      refresh();
    },
    [refresh],
  );

  const sendChat = useCallback(
    async (message: string): Promise<ChatReply> => {
      const reply = await sendJson<ChatReply>(ENDPOINTS.chat, "POST", { message });

      const at = new Date().toISOString();
      turnCount.current += 1;
      const id = turnCount.current;
      setTurns((previous) => [
        ...previous,
        { id: `local-user-${id}`, role: "user", content: message, actions: null, created_at: at },
        {
          id: `local-assistant-${id}`,
          role: "assistant",
          content: reply.message,
          actions: reply.actions,
          created_at: at,
        },
      ]);

      // Unconditional, not "only when `reply.actions` is non-empty": an action
      // that failed still tells the panels nothing changed, and the cost of
      // being wrong the other way is a heatmap that disagrees with the
      // transcript above it.
      refresh();
      return reply;
    },
    [refresh],
  );

  const chat = useMemo(
    () => [...(chatHistory.data?.messages ?? []), ...turns],
    [chatHistory.data, turns],
  );

  const account = useMemo<Account>(
    () => ({
      portfolio: portfolio.data,
      watchlist: watchlist.data?.tickers ?? [],
      history: history.data?.snapshots ?? [],
      chat,
      portfolioLoading: portfolio.loading,
      portfolioError: portfolio.error,
      watchlistLoading: watchlist.loading,
      watchlistError: watchlist.error,
      historyLoading: history.loading,
      historyError: history.error,
      chatLoading: chatHistory.loading,
      chatError: chatHistory.error,
      refresh,
      trade,
      addTicker,
      removeTicker,
      sendChat,
    }),
    [
      portfolio.data,
      portfolio.loading,
      portfolio.error,
      watchlist.data,
      watchlist.loading,
      watchlist.error,
      history.data,
      history.loading,
      history.error,
      chat,
      chatHistory.loading,
      chatHistory.error,
      refresh,
      trade,
      addTicker,
      removeTicker,
      sendChat,
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
