import { PageHeader } from "@/components/common/page-header";
import { MlActionBar } from "@/components/ml/ml-actions";
import {
  MlFeaturesCard,
  MlMetaCard,
  MlModelsCard,
  MlRankingCard,
  MlStatusCard,
} from "@/components/ml/ml-panels";

export default function MlPage() {
  return (
    <div className="space-y-4">
      <PageHeader
        title="Machine Learning"
        subtitle="Models, drift, ranking and features — ML advises, never decides"
        actions={<MlActionBar />}
      />
      <div className="grid gap-4 lg:grid-cols-2">
        <MlStatusCard />
        <MlMetaCard />
      </div>
      <MlModelsCard />
      <div className="grid gap-4 lg:grid-cols-2">
        <MlRankingCard />
        <MlFeaturesCard />
      </div>
    </div>
  );
}
