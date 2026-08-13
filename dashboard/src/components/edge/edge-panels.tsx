"use client";

import { useState } from "react";
import { Activity, Gauge, History, Loader2, RefreshCw, Scale } from "lucide-react";

import { SectionCard } from "@/components/common/section-card";
import { DictTable, MetricsGrid, renderScalar } from "@/components/common/metrics-grid";
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
import {
  useAttributionReport,
  useEdgeHistory,
  useEdgeReport,
  useEdgeStatus,
} from "@/lib/api/hooks";
import { useAttributionCycle, useEdgeCycle } from "@/lib/api/mutations";
import type { Dict } from "@/lib/api/types";
import { fmtDateTime, fmtNum } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * Colores por estado del edge. `degrading` exige dos motivos concurrentes —un
 * solo motivo es `watch`— así que el rojo significa algo y no se gasta en ruido.
 */
const STATUS_STYLE: Record<string, string> = {
  healthy: "border-bull/40 text-bull",
  watch: "border-warn/40 text-warn",
  degrading: "border-bear/50 text-bear",
};

/**
 * Una métrica no medible llega como `null` y se pinta como `—`, nunca como 0.
 * Colapsarla a cero es como se acaba penalizando a una estrategia por no tener
 * datos todavía (ADR-100).
 */
function Num({ value, decimals = 2 }: { value: unknown; decimals?: number }) {
  if (value === null || value === undefined) {
    return <span className="text-muted-foreground">—</span>;
  }
  if (typeof value !== "number") return <span>{renderScalar(value)}</span>;
  return <span>{fmtNum(value, { decimals })}</span>;
}

function rows(data: Dict): Dict[] {
  const list = data.strategies;
  return Array.isArray(list) ? (list as Dict[]) : [];
}

export function EdgeReportCard({
  onSelect,
  selected,
}: {
  onSelect: (strategy: string) => void;
  selected: string | null;
}) {
  const query = useEdgeReport();
  const cycle = useEdgeCycle();

  return (
    <SectionCard
      title="Edge health por estrategia"
      icon={<Activity />}
      contentClassName="pt-0"
      action={
        <Button size="xs" variant="ghost" onClick={() => cycle.mutate()} disabled={cycle.isPending}>
          {cycle.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />}
          Recalcular
        </Button>
      }
    >
      <Async
        query={query}
        disabledLabel="Edge Research Engine disabled"
        isEmpty={(d) => (d.status === "pending" ? true : rows(d).length === 0)}
        emptyLabel="Sin informe todavía"
        emptyDetail="El motor mide cada 15 minutos sobre resoluciones ya escritas. Pulsa Recalcular para forzar un ciclo."
      >
        {(data) => (
          <>
            <p className="pb-2 text-xs text-muted-foreground">
              Ventana rodante de 300 resoluciones · generado {fmtDateTime(String(data.generated_at))}
            </p>
            <Table>
              <TableHeader>
                <TableRow className="text-xs text-muted-foreground">
                  <TableHead>Estrategia</TableHead>
                  <TableHead>Estado</TableHead>
                  <TableHead className="text-right">Salud</TableHead>
                  <TableHead className="text-right">Muestra</TableHead>
                  <TableHead className="text-right">Expectancy R</TableHead>
                  <TableHead className="text-right">PF</TableHead>
                  <TableHead className="text-right">
                    <Tooltip>
                      <TooltipTrigger
                        render={<span className="cursor-help underline decoration-dotted">Decay</span>}
                      />
                      <TooltipContent>
                        Caída del edge entre el bloque más antiguo y el más reciente de la ventana.
                      </TooltipContent>
                    </Tooltip>
                  </TableHead>
                  <TableHead className="text-right">
                    <Tooltip>
                      <TooltipTrigger
                        render={
                          <span className="cursor-help underline decoration-dotted">Half-life</span>
                        }
                      />
                      <TooltipContent>
                        Extrapolación lineal, en operaciones. Es una alarma de orden de magnitud, no
                        una predicción.
                      </TooltipContent>
                    </Tooltip>
                  </TableHead>
                  <TableHead className="text-right">Estabilidad</TableHead>
                  <TableHead>Motivos</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows(data).map((row) => {
                  const name = String(row.strategy);
                  const status = String(row.status ?? "");
                  const reasons = Array.isArray(row.reasons) ? (row.reasons as string[]) : [];
                  return (
                    <TableRow
                      key={name}
                      onClick={() => onSelect(name)}
                      className={cn(
                        "cursor-pointer",
                        selected === name && "bg-muted/50",
                        status === "degrading" && "bg-bear/5",
                      )}
                    >
                      <TableCell className="font-medium">{name}</TableCell>
                      <TableCell>
                        <Badge variant="outline" className={STATUS_STYLE[status] ?? ""}>
                          {status || "—"}
                        </Badge>
                      </TableCell>
                      <TableCell className="tnum text-right">
                        <Num value={row.health_score} decimals={1} />
                      </TableCell>
                      <TableCell className="tnum text-right">
                        <Num value={row.sample} decimals={0} />
                      </TableCell>
                      <TableCell
                        className={cn(
                          "tnum text-right",
                          typeof row.expectancy_r === "number" &&
                            (row.expectancy_r < 0 ? "text-bear" : "text-bull"),
                        )}
                      >
                        <Num value={row.expectancy_r} />
                      </TableCell>
                      <TableCell className="tnum text-right">
                        <Num value={row.profit_factor} />
                      </TableCell>
                      <TableCell className="tnum text-right">
                        <Num value={row.edge_decay} />
                      </TableCell>
                      <TableCell className="tnum text-right">
                        <Num value={row.half_life_trades} decimals={1} />
                      </TableCell>
                      <TableCell className="tnum text-right">
                        <Num value={row.stability_score} />
                      </TableCell>
                      <TableCell className="max-w-xs text-xs text-muted-foreground">
                        {reasons.length > 0 ? reasons.join(" · ") : "—"}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </>
        )}
      </Async>
    </SectionCard>
  );
}

export function EdgeStatusCard() {
  const query = useEdgeStatus();
  return (
    <SectionCard title="Edge Research" icon={<Gauge />}>
      <Async query={query} disabledLabel="Edge Research Engine disabled">
        {(data) => <MetricsGrid data={data} columns={2} />}
      </Async>
    </SectionCard>
  );
}

export function EdgeHistoryCard({ strategy }: { strategy: string | null }) {
  const query = useEdgeHistory(strategy);
  return (
    <SectionCard
      title="Histórico del edge"
      icon={<History />}
      action={
        strategy ? <StatusChip label="Estrategia" value={strategy} /> : null
      }
    >
      {strategy === null ? (
        <p className="text-sm text-muted-foreground">
          Selecciona una estrategia en la tabla para ver su evolución.
        </p>
      ) : (
        <Async
          query={query}
          disabledLabel="Edge Research Engine disabled"
          isEmpty={(d) => !Array.isArray(d.points) || (d.points as unknown[]).length === 0}
          emptyLabel="Sin histórico para esta estrategia"
        >
          {(data) => <DictTable rows={(data.points as Dict[]) ?? []} />}
        </Async>
      )}
    </SectionCard>
  );
}

export function AttributionCard() {
  const query = useAttributionReport();
  const cycle = useAttributionCycle();
  return (
    <SectionCard
      title="Atribución del edge"
      icon={<Scale />}
      action={
        <Button size="xs" variant="ghost" onClick={() => cycle.mutate()} disabled={cycle.isPending}>
          {cycle.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />}
          Recalcular
        </Button>
      }
    >
      <Async query={query} disabledLabel="Edge Attribution Engine disabled">
        {(data) => (
          <div className="space-y-3">
            <p className="text-xs text-muted-foreground">
              Mide asociación, no causa. El residuo se reporta siempre: es la parte del resultado
              que ningún factor explica.
            </p>
            <MetricsGrid data={data} columns={3} />
            {Array.isArray(data.factors) && (data.factors as unknown[]).length > 0 && (
              <DictTable rows={data.factors as Dict[]} />
            )}
          </div>
        )}
      </Async>
    </SectionCard>
  );
}

/** Página de Edge: tabla + histórico de la estrategia seleccionada. */
export function EdgeView() {
  const [selected, setSelected] = useState<string | null>(null);
  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-2">
        <EdgeStatusCard />
        <AttributionCard />
      </div>
      <EdgeReportCard onSelect={setSelected} selected={selected} />
      <EdgeHistoryCard strategy={selected} />
    </div>
  );
}
