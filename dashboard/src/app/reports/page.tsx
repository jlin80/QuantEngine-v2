import { PageHeader } from "@/components/common/page-header";
import { ReportGenerator, SavedReports } from "@/components/reports/reports-view";

export default function ReportsPage() {
  return (
    <div className="space-y-4">
      <PageHeader title="Reports" subtitle="Generate and export reports (JSON / Markdown / CSV)" />
      <ReportGenerator />
      <SavedReports />
    </div>
  );
}
