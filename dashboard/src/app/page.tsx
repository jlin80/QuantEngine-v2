import { PageHeader } from "@/components/common/page-header";
import { AlertsCard, RecentEventsCard } from "@/components/overview/activity";
import { PerformanceCard } from "@/components/overview/performance-card";
import { PortfolioKpis } from "@/components/overview/portfolio-kpis";
import { RiskCard } from "@/components/overview/risk-card";
import { SystemHealthCard } from "@/components/overview/system-health-card";

export default function OverviewPage() {
  return (
    <div className="space-y-4">
      <PageHeader
        title="Overview"
        subtitle="Paper trading control center · live disabled"
      />
      <PortfolioKpis />
      <div className="grid gap-4 lg:grid-cols-3">
        <PerformanceCard />
        <SystemHealthCard />
        <RiskCard />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <RecentEventsCard />
        <AlertsCard />
      </div>
    </div>
  );
}
