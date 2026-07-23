import { PageHeader } from "@/components/common/page-header";
import { StrategiesPanel } from "@/components/strategies/strategies-panel";

export default function StrategiesPage() {
  return (
    <div className="space-y-4">
      <PageHeader
        title="Strategies"
        subtitle="Loaded strategies, weights and runtime metrics"
      />
      <StrategiesPanel />
    </div>
  );
}
