"use client";

import { Boxes, Loader2, Power, PowerOff } from "lucide-react";

import { SectionCard } from "@/components/common/section-card";
import { Async } from "@/components/common/states";
import { StatusChip } from "@/components/common/status-chip";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useStrategies } from "@/lib/api/hooks";
import { useStrategyDisable, useStrategyEnable } from "@/lib/api/mutations";
import type { StrategyStats } from "@/lib/api/types";
import { fmtAgo, fmtNum } from "@/lib/format";
import { cn } from "@/lib/utils";

function Row({
  s,
  explanation,
  onToggle,
  pending,
}: {
  s: StrategyStats;
  explanation?: string;
  onToggle: (name: string, enable: boolean) => void;
  pending: boolean;
}) {
  return (
    <TableRow className={cn(!s.enabled && "opacity-60")}>
      <TableCell className="font-medium">
        {explanation ? (
          <Tooltip>
            <TooltipTrigger render={<span className="cursor-help underline decoration-dotted">{s.name}</span>} />
            <TooltipContent className="max-w-sm">{explanation}</TooltipContent>
          </Tooltip>
        ) : (
          s.name
        )}
      </TableCell>
      <TableCell>
        <Badge variant="outline" className={s.enabled ? "border-bull/40 text-bull" : ""}>
          {s.enabled ? "enabled" : "disabled"}
        </Badge>
      </TableCell>
      <TableCell className="tnum text-right">{fmtNum(s.weight, { decimals: 2 })}</TableCell>
      <TableCell className="text-muted-foreground">{s.cadence || "—"}</TableCell>
      <TableCell className="tnum text-right">{s.runs}</TableCell>
      <TableCell className={cn("tnum text-right", s.errors > 0 && "text-bear")}>{s.errors}</TableCell>
      <TableCell className="tnum text-right">{s.signals_produced}</TableCell>
      <TableCell className="tnum text-right">{fmtNum(s.last_score, { decimals: 2 })}</TableCell>
      <TableCell className="tnum text-right">{fmtNum(s.last_confidence, { decimals: 2 })}</TableCell>
      <TableCell className="tnum text-right text-muted-foreground">
        {fmtNum(s.avg_duration_ms, { decimals: 1 })} ms
      </TableCell>
      <TableCell className="text-right text-muted-foreground">{fmtAgo(s.last_run_at)}</TableCell>
      <TableCell className="text-right">
        <Button
          size="xs"
          variant="ghost"
          onClick={() => onToggle(s.name, !s.enabled)}
          disabled={pending}
          title={s.enabled ? "Disable" : "Enable"}
        >
          {pending ? (
            <Loader2 className="animate-spin" />
          ) : s.enabled ? (
            <PowerOff className="text-bear" />
          ) : (
            <Power className="text-bull" />
          )}
        </Button>
      </TableCell>
    </TableRow>
  );
}

export function StrategiesPanel() {
  const query = useStrategies();
  const enable = useStrategyEnable();
  const disable = useStrategyDisable();
  const pendingName =
    enable.isPending ? enable.variables : disable.isPending ? disable.variables : null;
  const onToggle = (name: string, wantEnable: boolean) => {
    if (wantEnable) enable.mutate(name);
    else disable.mutate(name);
  };
  return (
    <SectionCard
      title="Strategies"
      icon={<Boxes />}
      contentClassName="pt-0"
      action={
        query.data ? (
          <StatusChip
            label="Loaded"
            value={`${query.data.strategies.filter((s) => s.enabled).length}/${query.data.strategies.length} active`}
          />
        ) : null
      }
    >
      <Async
        query={query}
        disabledLabel="Quant Core disabled"
        isEmpty={(d) => d.strategies.length === 0}
        emptyLabel="No strategies loaded"
      >
        {(d) => (
          <Table>
            <TableHeader>
              <TableRow className="text-xs text-muted-foreground">
                <TableHead>Strategy</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Weight</TableHead>
                <TableHead>Cadence</TableHead>
                <TableHead className="text-right">Runs</TableHead>
                <TableHead className="text-right">Errors</TableHead>
                <TableHead className="text-right">Signals</TableHead>
                <TableHead className="text-right">Score</TableHead>
                <TableHead className="text-right">Conf</TableHead>
                <TableHead className="text-right">Avg ms</TableHead>
                <TableHead className="text-right">Last run</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {[...d.strategies]
                .sort((a, b) => b.weight - a.weight)
                .map((s) => (
                  <Row
                    key={s.name}
                    s={s}
                    explanation={d.explanations[s.name]}
                    onToggle={onToggle}
                    pending={pendingName === s.name}
                  />
                ))}
            </TableBody>
          </Table>
        )}
      </Async>
    </SectionCard>
  );
}
