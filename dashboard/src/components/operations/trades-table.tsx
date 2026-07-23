"use client";

import { History } from "lucide-react";

import { SideBadge } from "@/components/common/cells";
import { SectionCard } from "@/components/common/section-card";
import { Async } from "@/components/common/states";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useTrades } from "@/lib/api/hooks";
import type { Trade } from "@/lib/api/types";
import { fmtAgo, fmtDuration, fmtMoney, fmtNum, fmtPct, fmtPrice, pnlClass } from "@/lib/format";

function TradeRow({ t }: { t: Trade }) {
  return (
    <TableRow>
      <TableCell className="font-medium">{t.symbol}</TableCell>
      <TableCell>
        <SideBadge side={t.side} />
      </TableCell>
      <TableCell className="tnum text-right">{fmtPrice(t.entry_price)}</TableCell>
      <TableCell className="tnum text-right">{fmtPrice(t.exit_price)}</TableCell>
      <TableCell className={`tnum text-right font-medium ${pnlClass(t.pnl)}`}>
        {fmtMoney(t.pnl, { sign: true })}
      </TableCell>
      <TableCell className={`tnum text-right ${pnlClass(t.r_multiple)}`}>
        {fmtNum(t.r_multiple, { sign: true })}
      </TableCell>
      <TableCell className={`tnum text-right ${pnlClass(t.return_pct)}`}>
        {fmtPct(t.return_pct, { sign: true })}
      </TableCell>
      <TableCell className="tnum text-right text-muted-foreground">
        {fmtMoney(t.commission)}
      </TableCell>
      <TableCell className="tnum text-right text-muted-foreground">
        {fmtNum(t.slippage_bps, { decimals: 1 })}
      </TableCell>
      <TableCell className="tnum text-right text-muted-foreground">
        {fmtNum(t.spread_bps, { decimals: 1 })}
      </TableCell>
      <TableCell className="text-muted-foreground capitalize">{t.regime ?? "—"}</TableCell>
      <TableCell className="tnum text-right text-muted-foreground">
        {fmtDuration(t.duration_seconds)}
      </TableCell>
      <TableCell className="text-right text-muted-foreground">{fmtAgo(t.exit_time)}</TableCell>
    </TableRow>
  );
}

export function RecentTradesCard() {
  const query = useTrades(60);
  return (
    <SectionCard title="Recent trades" icon={<History />} contentClassName="pt-0">
      <Async
        query={query}
        disabledLabel="Execution engine disabled"
        isEmpty={(d) => d.trades.length === 0}
        emptyLabel="No trades recorded"
        emptyDetail="Closed trades from the journal appear here."
      >
        {(d) => (
          <Table>
            <TableHeader>
              <TableRow className="text-xs text-muted-foreground">
                <TableHead>Symbol</TableHead>
                <TableHead>Side</TableHead>
                <TableHead className="text-right">Entry</TableHead>
                <TableHead className="text-right">Exit</TableHead>
                <TableHead className="text-right">PnL</TableHead>
                <TableHead className="text-right">R</TableHead>
                <TableHead className="text-right">Return</TableHead>
                <TableHead className="text-right">Comm</TableHead>
                <TableHead className="text-right">Slip bps</TableHead>
                <TableHead className="text-right">Spread bps</TableHead>
                <TableHead>Regime</TableHead>
                <TableHead className="text-right">Duration</TableHead>
                <TableHead className="text-right">Closed</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {[...d.trades].reverse().map((t) => (
                <TradeRow key={t.trade_id} t={t} />
              ))}
            </TableBody>
          </Table>
        )}
      </Async>
    </SectionCard>
  );
}
