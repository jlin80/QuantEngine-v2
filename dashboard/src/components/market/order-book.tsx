"use client";

import { BookOpen } from "lucide-react";

import { SectionCard } from "@/components/common/section-card";
import { Async } from "@/components/common/states";
import { useOrderBook } from "@/lib/api/hooks";
import type { DepthLevel } from "@/lib/api/types";
import { fmtNum, fmtPrice } from "@/lib/format";
import { cn } from "@/lib/utils";

function Ladder({
  levels,
  side,
  maxSize,
}: {
  levels: DepthLevel[];
  side: "bid" | "ask";
  maxSize: number;
}) {
  return (
    <div className="flex flex-col gap-px">
      {levels.map((l, i) => (
        <div key={i} className="relative flex items-center justify-between px-2 py-0.5 text-xs">
          <div
            className={cn("absolute inset-y-0 right-0 rounded-sm", side === "bid" ? "bg-bull/10" : "bg-bear/10")}
            style={{ width: `${maxSize > 0 ? (l.size / maxSize) * 100 : 0}%` }}
          />
          <span className={cn("tnum relative", side === "bid" ? "text-bull" : "text-bear")}>
            {fmtPrice(l.price)}
          </span>
          <span className="tnum relative text-muted-foreground">
            {fmtNum(l.size, { decimals: 3 })}
          </span>
        </div>
      ))}
    </div>
  );
}

export function OrderBookCard({ symbol }: { symbol: string | null }) {
  const query = useOrderBook(symbol, 14);
  return (
    <SectionCard title="Order book" icon={<BookOpen />} contentClassName="pt-2">
      <Async
        query={query}
        disabledLabel="Data engine disabled"
        isEmpty={(b) => b.bids.length === 0 && b.asks.length === 0}
        emptyLabel="No depth"
        emptyDetail="Order book appears when the feed provides depth."
      >
        {(b) => {
          const sizes = [...b.bids, ...b.asks].map((l) => l.size);
          const maxSize = sizes.length ? Math.max(...sizes) : 0;
          const asks = b.asks.slice(0, 12).reverse();
          const bids = b.bids.slice(0, 12);
          return (
            <div className="space-y-1">
              <Ladder levels={asks} side="ask" maxSize={maxSize} />
              <div className="flex items-center justify-between border-y border-border/60 px-2 py-1 text-xs">
                <span className="tnum font-medium">{fmtPrice(b.mid)}</span>
                <span className="text-muted-foreground">
                  spread {fmtNum(b.spread, { decimals: 4 })}
                </span>
              </div>
              <Ladder levels={bids} side="bid" maxSize={maxSize} />
            </div>
          );
        }}
      </Async>
    </SectionCard>
  );
}
