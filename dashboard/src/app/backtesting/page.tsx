import { PageHeader } from "@/components/common/page-header";
import {
  BacktestCriteriaCard,
  BacktestExperimentsCard,
  BacktestStatusCard,
} from "@/components/backtesting/backtesting-panels";
import { BacktestRunner } from "@/components/backtesting/backtest-runner";

export default function BacktestingPage() {
  return (
    <div className="space-y-4">
      <PageHeader title="Backtesting" subtitle="Run experiments, compare and qualify strategies" />
      <BacktestRunner />
      <div className="grid gap-4 lg:grid-cols-2">
        <BacktestStatusCard />
        <BacktestCriteriaCard />
      </div>
      <BacktestExperimentsCard />
    </div>
  );
}
