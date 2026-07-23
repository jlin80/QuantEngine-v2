import { PageHeader } from "@/components/common/page-header";
import {
  BackupsCard,
  OperationsCard,
  ProductionStatusCard,
  SecurityCard,
} from "@/components/production/production-view";

export default function ProductionPage() {
  return (
    <div className="space-y-4">
      <PageHeader
        title="Producción"
        subtitle="Live gating, seguridad, backups y operación 24/7 — live sigue deshabilitado"
      />
      <ProductionStatusCard />
      <div className="grid gap-4 lg:grid-cols-2">
        <SecurityCard />
        <BackupsCard />
      </div>
      <OperationsCard />
    </div>
  );
}
