import { PageHeader } from "@/components/common/page-header";
import { LogsView } from "@/components/logs/logs-view";

export default function LogsPage() {
  return (
    <div className="space-y-4">
      <PageHeader title="Logs" subtitle="Engine log stream, filterable by level" />
      <LogsView />
    </div>
  );
}
