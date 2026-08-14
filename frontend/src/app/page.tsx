"use client";

/**
 * The workstation — PLAN.md §10's layout, assembled.
 *
 * This is the one place where context meets props. Every panel below takes
 * data as arguments and holds no fetching of its own; what they share arrives
 * through `useMarket()` and `useAccount()`, so there is one price stream on
 * the page and one place that re-reads the account after a trade.
 *
 * The chat sidebar docks to the right of the grid, and collapses to a rail.
 * Collapsing changes one grid track: nothing else unmounts, so the sparklines
 * the page has accumulated and the chart's window survive it.
 *
 * ## Selection
 *
 * Which ticker is charted is the one piece of state that is purely about this
 * screen — no endpoint knows about it and nothing outside the layout reads it
 * — so it lives here rather than in the provider. It defaults to the first
 * watchlist row so the chart is never empty on a fresh load, and follows the
 * user from either the watchlist, the positions table, or the heatmap.
 */

import { useCallback, useMemo, useState } from "react";

import { FeedPanel } from "@/components/FeedPanel";
import { Header } from "@/components/Header";
import { PnlChart } from "@/components/PnlChart";
import { PortfolioHeatmap } from "@/components/PortfolioHeatmap";
import { PositionsTable } from "@/components/PositionsTable";
import { ChatPanel } from "@/components/ChatPanel";
import { PriceChart } from "@/components/PriceChart";
import { TradeBar } from "@/components/TradeBar";
import { WatchlistPanel } from "@/components/WatchlistPanel";
import { markPositions } from "@/lib/valuation";
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
  const [picked, setPicked] = useState<string | null>(null);
  const [chatCollapsed, setChatCollapsed] = useState(false);
  // Stable, so `ChatPanel`'s memo actually holds across the 2 Hz re-render this
  // component takes from `useMarket()`.
  const toggleChat = useCallback(() => setChatCollapsed((current) => !current), []);

  const positions = useMemo(
    () => markPositions(account.portfolio, market.prices),
    [account.portfolio, market.prices],
  );

  // Null means "whatever is at the top of the watchlist", which is what makes
  // the chart populated on first paint without a second render to set it.
  //
  // What may be charted is what the backend is still streaming: the watchlist
  // plus anything held, which is `app.watchlist.reconcile`'s "tracked" set. A
  // removed ticker that is still held keeps its chart, and a removed ticker
  // that is not falls back to the top of the list.
  //
  // The test worth stating: this must *not* be decided from `market.prices`.
  // That record is append-only by design — a frame omitting a ticker must not
  // blank its row — so a removed symbol keeps its last quote there forever,
  // and the chart would sit pinned to a frozen price with no row on screen to
  // explain where it came from.
  const watched = account.watchlist.map((row) => row.ticker);
  const chartable = new Set([...watched, ...positions.map((position) => position.ticker)]);
  const selected = picked !== null && chartable.has(picked) ? picked : (watched[0] ?? null);

  return (
    <div className="flex h-dvh flex-col bg-terminal text-ink">
      <Header
        portfolio={account.portfolio}
        prices={market.prices}
        status={market.status}
        stalled={market.stalled}
      />

      {/* The first three tracks are **identical in both strings**, so collapsing
          the assistant changes exactly one of them and the centre's `1fr`
          absorbs the difference. Nothing else remounts — and now nothing else
          is re-laid-out either, so the chart and the treemap keep their
          geometry rather than re-measuring on a toggle. */}
      <main
        className={`grid min-h-0 flex-1 grid-cols-1 gap-2 overflow-y-auto p-2 lg:overflow-hidden ${
          chatCollapsed
            ? "lg:grid-cols-[minmax(280px,320px)_1fr_minmax(280px,320px)_2.5rem]"
            : "lg:grid-cols-[minmax(280px,320px)_1fr_minmax(280px,320px)_minmax(300px,360px)]"
        }`}
      >
        {/* Left rail: what to watch. */}
        <div className="flex min-h-0 flex-col gap-2 lg:overflow-hidden">
          <div className="flex min-h-[14rem] flex-1 flex-col">
            <WatchlistPanel
              rows={account.watchlist}
              prices={market.prices}
              sparklines={market.sparklines}
              loading={account.watchlistLoading}
              error={account.watchlistError}
              selected={selected}
              onSelect={setPicked}
              onAdd={account.addTicker}
              onRemove={account.removeTicker}
            />
          </div>
          <FeedPanel
            status={market.status}
            stalled={market.stalled}
            frames={market.frames}
            lastFrameAt={market.lastFrameAt}
            tickerCount={market.pricedTickers}
            shocks={market.shocks}
          />
        </div>

        {/* Centre: the chart, then the order ticket under it. */}
        <div className="flex min-h-0 flex-col gap-2 lg:overflow-hidden">
          <div className="flex min-h-[16rem] flex-[3] flex-col">
            <PriceChart
              ticker={selected}
              quote={selected === null ? null : (market.prices[selected] ?? null)}
              points={selected === null ? [] : (market.sparklines[selected] ?? [])}
            />
          </div>
          <TradeBar onSubmit={account.trade} selected={selected} />
          <div className="flex min-h-[12rem] flex-[2] flex-col">
            <PositionsTable
              positions={positions}
              selected={selected}
              onSelect={setPicked}
              loading={account.portfolioLoading}
            />
          </div>
        </div>

        {/* Right rail: what it is all worth. */}
        <div className="flex min-h-0 flex-col gap-2 lg:overflow-hidden">
          <div className="flex min-h-[12rem] flex-1 flex-col">
            <PortfolioHeatmap
              positions={positions}
              selected={selected}
              onSelect={setPicked}
              loading={account.portfolioLoading}
            />
          </div>
          <div className="flex min-h-[12rem] flex-1 flex-col">
            <PnlChart
              snapshots={account.history}
              loading={account.historyLoading}
              error={account.historyError}
            />
          </div>
        </div>

        {/* Right dock: the copilot. */}
        <ChatPanel
          messages={account.chat}
          onSend={account.sendChat}
          onRefresh={account.refresh}
          collapsed={chatCollapsed}
          onToggle={toggleChat}
          loading={account.chatLoading}
          error={account.chatError}
        />
      </main>
    </div>
  );
}
