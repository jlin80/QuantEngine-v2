"use client";

import { CandlestickChart } from "lucide-react";

import { SectionCard } from "@/components/common/section-card";
import { Async } from "@/components/common/states";
import { Skeleton } from "@/components/ui/skeleton";
import { useCandles } from "@/lib/api/hooks";
import { useRealtimeStore } from "@/lib/store/realtime";
import { PriceChart } from "./price-chart";

export function PriceChartCard({ symbol }: { symbol: string | null }) {
  const query = useCandles(symbol, "1m", 200);
  const livePrice = useRealtimeStore((s) =>
    symbol ? (s.lastPrices[symbol]?.price ?? null) : null,
  );

  return (
    <SectionCard
      title={
        <span className="flex items-center gap-2">
          {symbol ?? "—"}
          <span className="text-xs font-normal text-muted-foreground">1m</span>
        </span>
      }
      icon={<CandlestickChart />}
    >
      <Async
        query={query}
        disabledLabel="Data engine disabled"
        isEmpty={(d) => d.candles.length === 0}
        emptyLabel="No candles"
        emptyDetail="Price history appears once the feed aggregates candles."
        skeleton={<Skeleton className="h-[380px] w-full rounded-md" />}
      >
        {(d) => <PriceChart candles={d.candles} livePrice={livePrice} />}
      </Async>
    </SectionCard>
  );
}
