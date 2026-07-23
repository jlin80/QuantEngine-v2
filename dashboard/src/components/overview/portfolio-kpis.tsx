"use client";

import { Async } from "@/components/common/states";
import { Sparkline } from "@/components/common/sparkline";
import { StatTile } from "@/components/common/stat";
import { Skeleton } from "@/components/ui/skeleton";
import { usePortfolio } from "@/lib/api/hooks";
import type { PortfolioSnapshot } from "@/lib/api/types";
import { useLiveSeries } from "@/lib/hooks/use-live-series";
import { fmtMoney, fmtPct, pnlClass } from "@/lib/format";

function KpiSkeleton() {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
      {Array.from({ length: 6 }).map((_, i) => (
        <Skeleton key={i} className="h-[74px] w-full rounded-lg" />
      ))}
    </div>
  );
}

function KpiGrid({ p }: { p: PortfolioSnapshot }) {
  const equitySeries = useLiveSeries("equity", p.equity);
  const pnlSeries = useLiveSeries("floating_pnl", p.floating_pnl);

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
      <StatTile
        label="Equity"
        value={fmtMoney(p.equity)}
        sub={`Balance ${fmtMoney(p.balance)}`}
        icon={<Sparkline data={equitySeries} className="h-6 w-16" />}
      />
      <StatTile
        label="Floating PnL"
        value={fmtMoney(p.floating_pnl, { sign: true })}
        valueClassName={pnlClass(p.floating_pnl)}
        icon={<Sparkline data={pnlSeries} className="h-6 w-16" />}
      />
      <StatTile
        label="Realized PnL"
        value={fmtMoney(p.realized_pnl, { sign: true })}
        valueClassName={pnlClass(p.realized_pnl)}
      />
      <StatTile
        label="Return"
        value={fmtPct(p.return_pct, { sign: true })}
        valueClassName={pnlClass(p.return_pct)}
      />
      <StatTile
        label="Drawdown"
        value={fmtPct(p.drawdown_pct)}
        valueClassName={p.drawdown_pct > 0 ? "text-bear" : undefined}
        sub={`Peak ${fmtMoney(p.peak_equity)}`}
      />
      <StatTile
        label="Exposure"
        value={fmtPct(p.exposure_pct)}
        sub={`${p.open_positions} open · ${p.total_trades} trades`}
      />
    </div>
  );
}

export function PortfolioKpis() {
  const query = usePortfolio();
  return (
    <Async
      query={query}
      disabledLabel="Execution engine disabled"
      skeleton={<KpiSkeleton />}
    >
      {(p) => <KpiGrid p={p} />}
    </Async>
  );
}
