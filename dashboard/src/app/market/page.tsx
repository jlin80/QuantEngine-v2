"use client";

import { PageHeader } from "@/components/common/page-header";
import { OrderBookCard } from "@/components/market/order-book";
import { PriceChartCard } from "@/components/market/price-chart-card";
import { SymbolStats } from "@/components/market/symbol-stats";
import { SymbolsPanel } from "@/components/market/symbols-panel";
import { useActiveSymbol } from "@/components/market/use-active-symbol";

export default function MarketPage() {
  const symbol = useActiveSymbol();

  return (
    <div className="space-y-4">
      <PageHeader title="Market" subtitle="Live prices, depth and order flow" />
      <div className="grid gap-4 xl:grid-cols-[260px_1fr_300px]">
        <SymbolsPanel />
        <div className="min-w-0 space-y-4">
          <PriceChartCard symbol={symbol} />
          <SymbolStats symbol={symbol} />
        </div>
        <OrderBookCard symbol={symbol} />
      </div>
    </div>
  );
}
