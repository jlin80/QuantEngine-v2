import { PageHeader } from "@/components/common/page-header";
import { AlertsFeed, AuditTrail } from "@/components/alerts/alerts-view";

export default function AlertsPage() {
  return (
    <div className="space-y-4">
      <PageHeader title="Alerts" subtitle="Live risk & system alerts and the audit trail" />
      <div className="grid gap-4 lg:grid-cols-2">
        <AlertsFeed />
        <AuditTrail />
      </div>
    </div>
  );
}
