"use client";

import { FlaskConical, Gauge, ListChecks } from "lucide-react";

import { DictTable, MetricsGrid } from "@/components/common/metrics-grid";
import { SectionCard } from "@/components/common/section-card";
import { Async } from "@/components/common/states";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  useBacktestCriteria,
  useBacktestExperiments,
  useBacktestStatus,
} from "@/lib/api/hooks";

export function BacktestStatusCard() {
  const query = useBacktestStatus();
  return (
    <SectionCard title="Lab status" icon={<FlaskConical />}>
      <Async query={query} disabledLabel="Backtesting lab disabled">
        {(d) => <MetricsGrid data={d} columns={2} />}
      </Async>
    </SectionCard>
  );
}

export function BacktestCriteriaCard() {
  const query = useBacktestCriteria();
  return (
    <SectionCard title="Qualification criteria" icon={<Gauge />}>
      <Async query={query} disabledLabel="Backtesting lab disabled">
        {(d) => <MetricsGrid data={d} columns={2} />}
      </Async>
    </SectionCard>
  );
}

export function BacktestExperimentsCard() {
  const query = useBacktestExperiments(50);
  return (
    <SectionCard
      title="Experiments"
      icon={<ListChecks />}
      contentClassName="pt-0"
      action={query.data ? <span className="text-xs text-muted-foreground">{query.data.count} total</span> : null}
    >
      <Async
        query={query}
        disabledLabel="Backtesting lab disabled"
        isEmpty={(d) => d.experiments.length === 0}
        emptyLabel="No experiments yet"
        emptyDetail="Run a backtest to record an experiment."
      >
        {(d) => (
          <ScrollArea className="max-h-[420px]">
            <DictTable rows={d.experiments} maxColumns={10} />
          </ScrollArea>
        )}
      </Async>
    </SectionCard>
  );
}
