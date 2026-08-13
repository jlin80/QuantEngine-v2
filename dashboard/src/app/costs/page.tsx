import { PageHeader } from "@/components/common/page-header";
import { CostsDailyCard, CostsReportCard } from "@/components/edge/costs-panels";

export default function CostsPage() {
  return (
    <div className="space-y-4">
      <PageHeader
        title="Costes"
        subtitle="Comisiones, slippage, spread, residuo y coste de oportunidad"
      />
      <CostsReportCard />
      <CostsDailyCard />
    </div>
  );
}
