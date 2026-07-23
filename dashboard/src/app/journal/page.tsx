import { PageHeader } from "@/components/common/page-header";
import { JournalView } from "@/components/journal/journal-view";

export default function JournalPage() {
  return (
    <div className="space-y-4">
      <PageHeader title="Trade Journal" subtitle="Every closed trade with its full decision context" />
      <JournalView />
    </div>
  );
}
