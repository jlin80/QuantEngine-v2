"use client";

import { Gauge } from "lucide-react";

import { SectionCard } from "@/components/common/section-card";
import { Metric } from "@/components/common/stat";
import { Async } from "@/components/common/states";
import { usePerformance } from "@/lib/api/hooks";
import { fmtMoney, fmtNum, fmtPct, fmtRatioPct, pnlClass } from "@/lib/format";

export function PerformanceCard() {
  const query = usePerformance();
  return (
    <SectionCard title="Performance" icon={<Gauge />}>
      <Async
        query={query}
        disabledLabel="Execution engine disabled"
        isEmpty={(p) => p.total_trades === 0}
        emptyLabel="No closed trades yet"
        emptyDetail="Metrics appear once the paper engine closes its first trade."
      >
        {(p) => (
          <div className="grid grid-cols-2 gap-x-6">
            <Metric label="Win rate" value={fmtRatioPct(p.win_rate)} />
            <Metric label="Profit factor" value={fmtNum(p.profit_factor)} />
            <Metric
              label="Expectancy"
              value={fmtMoney(p.expectancy, { sign: true })}
              valueClassName={pnlClass(p.expectancy)}
            />
            <Metric
              label="Expectancy (R)"
              value={fmtNum(p.expectancy_r, { sign: true })}
              valueClassName={pnlClass(p.expectancy_r)}
            />
            <Metric label="Sharpe" value={fmtNum(p.sharpe)} />
            <Metric label="Sortino" value={fmtNum(p.sortino)} />
            <Metric label="Calmar" value={fmtNum(p.calmar)} />
            <Metric label="Max drawdown" value={fmtPct(p.max_drawdown_pct)} valueClassName="text-bear" />
            <Metric label="Risk / reward" value={fmtNum(p.risk_reward)} />
            <Metric label="Record" value={`${p.wins}W / ${p.losses}L`} />
          </div>
        )}
      </Async>
    </SectionCard>
  );
}
