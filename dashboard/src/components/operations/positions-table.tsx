"use client";

import { Briefcase } from "lucide-react";

import { Flag, SideBadge } from "@/components/common/cells";
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
import { usePositions } from "@/lib/api/hooks";
import type { Position } from "@/lib/api/types";
import { fmtDuration, fmtMoney, fmtNum, fmtPrice, pnlClass } from "@/lib/format";

function ageSeconds(iso: string | null): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : (Date.now() - t) / 1000;
}

function PositionRow({ p }: { p: Position }) {
  return (
    <TableRow>
      <TableCell className="font-medium">{p.symbol}</TableCell>
      <TableCell>
        <SideBadge side={p.side} />
      </TableCell>
      <TableCell className="tnum text-right">{fmtNum(p.quantity, { decimals: 4 })}</TableCell>
      <TableCell className="tnum text-right">{fmtPrice(p.entry_price)}</TableCell>
      <TableCell className="tnum text-right">{fmtPrice(p.mark_price)}</TableCell>
      <TableCell className="tnum text-bear/80 text-right">{fmtPrice(p.stop_loss)}</TableCell>
      <TableCell className="tnum text-bull/80 text-right">{fmtPrice(p.take_profit)}</TableCell>
      <TableCell className={`tnum text-right font-medium ${pnlClass(p.unrealized_pnl)}`}>
        {fmtMoney(p.unrealized_pnl, { sign: true })}
      </TableCell>
      <TableCell className={`tnum text-right ${pnlClass(p.r_multiple)}`}>
        {fmtNum(p.r_multiple, { sign: true })}
      </TableCell>
      <TableCell className="tnum text-right">{fmtNum(p.score, { decimals: 2 })}</TableCell>
      <TableCell>
        <div className="flex gap-1">
          {p.break_even_active && <Flag label="BE" tone="bull" />}
          {p.trailing_active && <Flag label="TR" tone="info" />}
        </div>
      </TableCell>
      <TableCell className="tnum text-right text-muted-foreground">
        {fmtDuration(ageSeconds(p.opened_at))}
      </TableCell>
    </TableRow>
  );
}

export function OpenPositionsCard() {
  const query = usePositions();
  return (
    <SectionCard title="Open positions" icon={<Briefcase />} contentClassName="pt-0">
      <Async
        query={query}
        disabledLabel="Execution engine disabled"
        isEmpty={(d) => d.open.length === 0}
        emptyLabel="No open positions"
        emptyDetail="Live positions appear here as the paper engine opens them."
      >
        {(d) => (
          <Table>
            <TableHeader>
              <TableRow className="text-xs text-muted-foreground">
                <TableHead>Symbol</TableHead>
                <TableHead>Side</TableHead>
                <TableHead className="text-right">Qty</TableHead>
                <TableHead className="text-right">Entry</TableHead>
                <TableHead className="text-right">Mark</TableHead>
                <TableHead className="text-right">SL</TableHead>
                <TableHead className="text-right">TP</TableHead>
                <TableHead className="text-right">uPnL</TableHead>
                <TableHead className="text-right">R</TableHead>
                <TableHead className="text-right">Score</TableHead>
                <TableHead>Flags</TableHead>
                <TableHead className="text-right">Age</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {d.open.map((p) => (
                <PositionRow key={p.position_id} p={p} />
              ))}
            </TableBody>
          </Table>
        )}
      </Async>
    </SectionCard>
  );
}
