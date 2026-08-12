"use client";

/**
 * The terminal header — PLAN.md §10: portfolio total updating live, cash
 * balance, and the connection status dot.
 *
 * The total is marked against the stream rather than re-fetched, so it moves
 * with the prices. When a held ticker has no price at all the total says so
 * instead of quietly shrinking — see `lib/valuation.ts`.
 */

import { ConnectionDot } from "@/components/ConnectionDot";
import type { ConnectionStatus } from "@/hooks/usePriceStream";
import { formatDollars, formatDollarsCompact, formatPercent, toneClass } from "@/lib/format";
import type { Portfolio, Quote } from "@/lib/types";
import { valuePortfolio } from "@/lib/valuation";

interface HeaderProps {
  portfolio: Portfolio | null;
  prices: Record<string, Quote>;
  status: ConnectionStatus;
}

export function Header({ portfolio, prices, status }: HeaderProps) {
  const live = valuePortfolio(portfolio, prices);

  // Against what was paid for the positions actually marked — `live.costBasis`,
  // never `portfolio.cost_basis`. The endpoint's figure covers whichever
  // positions had a price when it was fetched, and dividing a live numerator
  // by that set's cost gives a number that can be off by tens of percent.
  const investedPnl =
    live === null || live.costBasis === 0
      ? null
      : ((live.positionsValue - live.costBasis) / live.costBasis) * 100;

  return (
    <header className="flex flex-wrap items-center gap-x-8 gap-y-2 border-b border-edge bg-panel px-4 py-2.5">
      <h1 className="flex items-baseline gap-2 font-semibold tracking-tight">
        <span className="text-lg text-accent">FinAlly</span>
        <span className="hidden text-[10px] tracking-[0.18em] text-faint uppercase sm:inline">
          Trading Workstation
        </span>
      </h1>

      <dl className="flex flex-wrap items-center gap-x-7 gap-y-1">
        <Stat label="Portfolio" testId="header-total">
          <span className="text-base font-semibold text-ink">
            {formatDollarsCompact(live?.totalValue)}
          </span>
          {investedPnl !== null && (
            <span className={`ml-2 text-xs ${toneClass(investedPnl)}`}>
              {formatPercent(investedPnl)}
            </span>
          )}
        </Stat>

        <Stat label="Cash" testId="header-cash">
          <span className="text-sm text-ink">{formatDollars(portfolio?.cash_balance)}</span>
        </Stat>

        {live !== null && live.unpricedTickers.length > 0 && (
          <Stat label="Unpriced" testId="header-unpriced">
            <span className="text-sm text-accent" title="Excluded from the total — no price yet">
              {live.unpricedTickers.join(" ")}
            </span>
          </Stat>
        )}
      </dl>

      <div className="ml-auto">
        <ConnectionDot status={status} />
      </div>
    </header>
  );
}

function Stat({
  label,
  testId,
  children,
}: {
  label: string;
  testId: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col">
      <dt className="text-[10px] tracking-[0.12em] text-faint uppercase">{label}</dt>
      <dd className="font-mono leading-tight" data-testid={testId}>
        {children}
      </dd>
    </div>
  );
}
