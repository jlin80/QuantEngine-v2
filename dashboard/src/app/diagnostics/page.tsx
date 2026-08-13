import { PageHeader } from "@/components/common/page-header";
import {
  BenchmarkCard,
  CorrelationCard,
  DataQualityCard,
  ExecutionOptimizerCard,
  MetaRiskCard,
  MicrostructureCard,
  PortfolioIntelligenceCard,
  RegimeForecastCard,
} from "@/components/edge/diagnostics-panels";

export default function DiagnosticsPage() {
  return (
    <div className="space-y-4">
      <PageHeader
        title="Diagnóstico"
        subtitle="Calidad del dato, meta riesgo, régimen, correlación y microestructura"
      />
      <div className="grid gap-4 lg:grid-cols-2">
        <DataQualityCard />
        <MetaRiskCard />
        <RegimeForecastCard />
        <CorrelationCard />
        <PortfolioIntelligenceCard />
        <MicrostructureCard />
        <ExecutionOptimizerCard />
        <BenchmarkCard />
      </div>
    </div>
  );
}
