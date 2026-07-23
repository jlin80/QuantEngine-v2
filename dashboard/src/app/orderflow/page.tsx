"use client";

import { PageHeader } from "@/components/common/page-header";
import { OrderBookCard } from "@/components/market/order-book";
import { SymbolsPanel } from "@/components/market/symbols-panel";
import { useActiveSymbol } from "@/components/market/use-active-symbol";
import { FlowStats } from "@/components/orderflow/flow-stats";
import { TradesTape } from "@/components/orderflow/trades-tape";

export default function OrderFlowPage() {
  const symbol = useActiveSymbol();

  return (
    <div className="space-y-4">
      <PageHeader title="Order Flow" subtitle="Depth, delta/CVD and time & sales" />
      <div className="grid gap-4 xl:grid-cols-[240px_1fr_300px]">
        <SymbolsPanel />
        <div className="min-w-0 space-y-4">
          <FlowStats symbol={symbol} />
          <TradesTape symbol={symbol} />
        </div>
        <OrderBookCard symbol={symbol} />
      </div>
    </div>
  );
}
