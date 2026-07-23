"use client";

import { Waves } from "lucide-react";

import { SectionCard } from "@/components/common/section-card";
import { StatTile } from "@/components/common/stat";
import { Async } from "@/components/common/states";
import { useMarketSnapshot, useOrderBook } from "@/lib/api/hooks";
import { fmtNum, fmtPrice } from "@/lib/format";
import { pnlClass } from "@/lib/format";

function sum(nums: number[]): number {
  return nums.reduce((a, b) => a + b, 0);
}

export function FlowStats({ symbol }: { symbol: string | null }) {
  const book = useOrderBook(symbol, 25);
  const snap = useMarketSnapshot(symbol);

  return (
    <SectionCard title="Order flow" icon={<Waves />}>
      <Async query={book} disabledLabel="Data engine disabled">
        {(b) => {
          const s = snap.data;
          const bidVol = sum(b.bids.map((l) => l.size));
          const askVol = sum(b.asks.map((l) => l.size));
          return (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
                <StatTile label="Bid volume" value={fmtNum(bidVol, { decimals: 3 })} valueClassName="text-bull" />
                <StatTile label="Ask volume" value={fmtNum(askVol, { decimals: 3 })} valueClassName="text-bear" />
                <StatTile
                  label="Delta"
                  value={fmtNum((s?.delta as number | undefined) ?? null, { decimals: 2 })}
                  valueClassName={pnlClass((s?.delta as number | undefined) ?? null)}
                />
                <StatTile label="CVD" value={fmtNum((s?.cvd as number | undefined) ?? null, { decimals: 2 })} />
                <StatTile label="Book pressure" value={fmtNum(b.book_pressure, { decimals: 2 })} />
                <StatTile label="Imbalance" value={fmtNum(b.imbalance, { decimals: 2 })} />
                <StatTile label="Microprice" value={fmtPrice(b.microprice)} />
                <StatTile
                  label="Absorption"
                  value={fmtNum((s?.absorption as number | undefined) ?? null, { decimals: 2 })}
                />
                <StatTile label="Best bid" value={fmtPrice(b.best_bid)} valueClassName="text-bull" />
                <StatTile label="Best ask" value={fmtPrice(b.best_ask)} valueClassName="text-bear" />
                <StatTile label="Spread" value={fmtNum(b.spread, { decimals: 4 })} />
                <StatTile label="Sequence" value={fmtNum(b.sequence, { decimals: 0 })} />
              </div>
              <p className="text-[11px] text-muted-foreground">
                Spoofing / iceberg / heatmap detection are experimental in the engine (no
                level-by-level book history) and surface here when the feed provides them.
              </p>
            </div>
          );
        }}
      </Async>
    </SectionCard>
  );
}
