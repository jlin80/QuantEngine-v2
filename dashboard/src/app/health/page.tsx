import { PageHeader } from "@/components/common/page-header";
import {
  ComponentsCard,
  FeedCard,
  RecentErrorsCard,
  VitalsCard,
} from "@/components/health/health-view";

export default function HealthPage() {
  return (
    <div className="space-y-4">
      <PageHeader title="Health Center" subtitle="System vitals, components, feed and errors" />
      <VitalsCard />
      <div className="grid gap-4 lg:grid-cols-2">
        <ComponentsCard />
        <FeedCard />
      </div>
      <RecentErrorsCard />
    </div>
  );
}
