"use client";

import { BarChart3 } from "lucide-react";

import { SectionCard } from "@/components/common/section-card";
import { StatTile } from "@/components/common/stat";
import { Async } from "@/components/common/states";
import { useMarketSnapshot, useOrderBook, useTicker } from "@/lib/api/hooks";
import { fmtNum, fmtPrice } from "@/lib/format";

export function SymbolStats({ symbol }: { symbol: string | null }) {
  const ticker = useTicker(symbol);
  const book = useOrderBook(symbol, 20);
  const snap = useMarketSnapshot(symbol);

  return (
    <SectionCard title="Market stats" icon={<BarChart3 />}>
      <Async query={ticker} disabledLabel="Data engine disabled">
        {(t) => {
          const b = book.data;
          const s = snap.data;
          return (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
              <StatTile label="Last" value={fmtPrice(t.last ?? t.mid)} />
              <StatTile label="Bid" value={fmtPrice(t.bid)} valueClassName="text-bull" />
              <StatTile label="Ask" value={fmtPrice(t.ask)} valueClassName="text-bear" />
              <StatTile label="Spread bps" value={fmtNum(t.spread_bps, { decimals: 2 })} />
              <StatTile label="Microprice" value={fmtPrice(b?.microprice ?? null)} />
              <StatTile label="Imbalance" value={fmtNum(b?.imbalance ?? null, { decimals: 2 })} />
              <StatTile label="Book pressure" value={fmtNum(b?.book_pressure ?? null, { decimals: 2 })} />
              <StatTile label="Latency ms" value={fmtNum(t.latency_ms, { decimals: 0 })} />
              <StatTile label="Regime" value={s?.regime ?? "—"} valueClassName="text-base capitalize" />
              <StatTile label="Volatility" value={fmtNum(s?.volatility ?? null, { decimals: 4 })} />
              <StatTile label="ATR" value={fmtNum(s?.atr ?? null, { decimals: 4 })} />
              <StatTile label="VWAP" value={fmtPrice(s?.vwap ?? null)} />
              <StatTile label="Delta" value={fmtNum(s?.delta ?? null, { decimals: 2 })} />
              <StatTile label="CVD" value={fmtNum(s?.cvd ?? null, { decimals: 2 })} />
            </div>
          );
        }}
      </Async>
    </SectionCard>
  );
}
