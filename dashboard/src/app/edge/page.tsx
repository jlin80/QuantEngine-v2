import { PageHeader } from "@/components/common/page-header";
import { EdgeView } from "@/components/edge/edge-panels";

export default function EdgePage() {
  return (
    <div className="space-y-4">
      <PageHeader
        title="Edge Research"
        subtitle="¿Sigue funcionando cada estrategia? — decay, half-life y estabilidad sobre ventana rodante"
      />
      <EdgeView />
    </div>
  );
}
