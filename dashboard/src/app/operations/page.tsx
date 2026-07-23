import { PageHeader } from "@/components/common/page-header";
import { OrdersStreamCard } from "@/components/operations/orders-stream";
import { OpenPositionsCard } from "@/components/operations/positions-table";
import { RecentTradesCard } from "@/components/operations/trades-table";

export default function OperationsPage() {
  return (
    <div className="space-y-4">
      <PageHeader title="Operations" subtitle="Positions, orders and trade history (paper)" />
      <OpenPositionsCard />
      <div className="grid gap-4 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <RecentTradesCard />
        </div>
        <OrdersStreamCard />
      </div>
    </div>
  );
}
