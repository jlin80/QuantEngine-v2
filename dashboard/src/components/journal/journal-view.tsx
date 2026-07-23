"use client";

import { NotebookText } from "lucide-react";

import { SideBadge } from "@/components/common/cells";
import { SectionCard } from "@/components/common/section-card";
import { Async } from "@/components/common/states";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useTrades } from "@/lib/api/hooks";
import type { Trade } from "@/lib/api/types";
import { fmtDateTime, fmtDuration, fmtMoney, fmtNum, fmtPct, fmtPrice, pnlClass } from "@/lib/format";
import { cn } from "@/lib/utils";

function Cell({ label, value, className }: { label: string; value: React.ReactNode; className?: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] tracking-wide text-muted-foreground uppercase">{label}</span>
      <span className={cn("tnum text-sm", className)}>{value}</span>
    </div>
  );
}

function TradeCard({ t }: { t: Trade }) {
  return (
    <div className="rounded-lg border border-border/70 bg-card/40 p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="font-semibold">{t.symbol}</span>
          <SideBadge side={t.side} />
          {t.regime && <span className="text-xs text-muted-foreground capitalize">{t.regime}</span>}
        </div>
        <span className={cn("tnum text-sm font-semibold", pnlClass(t.pnl))}>
          {fmtMoney(t.pnl, { sign: true })}
        </span>
      </div>
      <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
        <Cell label="Entry" value={fmtPrice(t.entry_price)} />
        <Cell label="Exit" value={fmtPrice(t.exit_price)} />
        <Cell label="R" value={fmtNum(t.r_multiple, { sign: true })} className={pnlClass(t.r_multiple)} />
        <Cell label="Return" value={fmtPct(t.return_pct, { sign: true })} className={pnlClass(t.return_pct)} />
        <Cell label="Score" value={fmtNum(t.score, { decimals: 2 })} />
        <Cell label="Confidence" value={fmtNum(t.confidence ?? null, { decimals: 2 })} />
        <Cell label="Commission" value={fmtMoney(t.commission)} />
        <Cell label="Slip / Spread" value={`${fmtNum(t.slippage_bps, { decimals: 1 })} / ${fmtNum(t.spread_bps, { decimals: 1 })}`} />
        <Cell label="Duration" value={fmtDuration(t.duration_seconds)} />
        <Cell label="ATR" value={fmtNum(t.atr ?? null, { decimals: 4 })} />
        <Cell label="Entry time" value={fmtDateTime(t.entry_time)} className="text-xs" />
        <Cell label="Exit time" value={fmtDateTime(t.exit_time)} className="text-xs" />
      </div>
    </div>
  );
}

export function JournalView() {
  const trades = useTrades(100);
  return (
    <SectionCard title="Trade journal" icon={<NotebookText />} contentClassName="pt-2">
      <Async
        query={trades}
        disabledLabel="Execution engine disabled"
        isEmpty={(d) => d.trades.length === 0}
        emptyLabel="No trades recorded"
        emptyDetail="Each closed trade is logged with its full decision context."
      >
        {(d) => (
          <ScrollArea className="h-[70vh]">
            <div className="grid gap-3 pr-3 md:grid-cols-2">
              {[...d.trades].reverse().map((t) => (
                <TradeCard key={t.trade_id} t={t} />
              ))}
            </div>
          </ScrollArea>
        )}
      </Async>
    </SectionCard>
  );
}
