import { PageHeader } from "@/components/common/page-header";
import {
  RejectionsRecentCard,
  RejectionsStatusCard,
  RejectionsSummaryCard,
} from "@/components/edge/rejections-panels";

export default function RejectionsPage() {
  return (
    <div className="space-y-4">
      <PageHeader
        title="Why Not Trade"
        subtitle="Qué puerta está bloqueando operaciones, y cuáles lo hacen en solitario"
      />
      <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
        <RejectionsSummaryCard />
        <RejectionsStatusCard />
      </div>
      <RejectionsRecentCard />
    </div>
  );
}
