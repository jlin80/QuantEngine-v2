"use client";

import { Ban, ListFilter, ShieldQuestion } from "lucide-react";

import { SectionCard } from "@/components/common/section-card";
import { DictTable, MetricsGrid } from "@/components/common/metrics-grid";
import { Async } from "@/components/common/states";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useRejections, useRejectionsStatus, useRejectionsSummary } from "@/lib/api/hooks";
import type { Dict } from "@/lib/api/types";
import { cn } from "@/lib/utils";

function counts(value: unknown): Record<string, number> {
  return value && typeof value === "object" ? (value as Record<string, number>) : {};
}

/**
 * Las dos cuentas del Why Not Trade Engine, lado a lado y en ese orden por una
 * razón: `blocked_by` es la que se mira por instinto y la que engaña. Una puerta
 * que siempre bloquea acompañada de otras tres no está costando nada — quitarla
 * no habría dejado pasar ni una operación. La columna que decide es la segunda.
 */
export function RejectionsSummaryCard() {
  const query = useRejectionsSummary();
  return (
    <SectionCard title="Qué está costando operaciones" icon={<Ban />} contentClassName="pt-0">
      <Async
        query={query}
        disabledLabel="Why Not Trade Engine disabled"
        isEmpty={(d) => Object.keys(counts(d.blocked_by)).length === 0}
        emptyLabel="Ningún rechazo registrado todavía"
      >
        {(data) => {
          const blocked = counts(data.blocked_by);
          const sole = counts(data.sole_blocker);
          const gates = Object.keys(blocked);
          const sample = Number(data.sample ?? 0);
          return (
            <>
              <p className="py-2 text-xs text-muted-foreground">
                Sobre {sample} rechazos en memoria · {String(data.no_opportunity ?? 0)} evaluaciones
                que ni llegaron a ser una oportunidad (no las bloqueó ningún filtro).
              </p>
              <Table>
                <TableHeader>
                  <TableRow className="text-xs text-muted-foreground">
                    <TableHead>Puerta</TableHead>
                    <TableHead className="text-right">
                      <Tooltip>
                        <TooltipTrigger
                          render={
                            <span className="cursor-help underline decoration-dotted">Bloqueó</span>
                          }
                        />
                        <TooltipContent>
                          Veces que esta puerta vetó, sola o acompañada.
                        </TooltipContent>
                      </Tooltip>
                    </TableHead>
                    <TableHead className="text-right">
                      <Tooltip>
                        <TooltipTrigger
                          render={
                            <span className="cursor-help underline decoration-dotted">
                              Única culpable
                            </span>
                          }
                        />
                        <TooltipContent>
                          Veces que fue la única que vetó. Es la cifra que decide si relajarla:
                          sólo estas operaciones habrían pasado sin ella.
                        </TooltipContent>
                      </Tooltip>
                    </TableHead>
                    <TableHead className="text-right">% del total</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {gates.map((gate) => {
                    const alone = sole[gate] ?? 0;
                    const share = sample > 0 ? (alone / sample) * 100 : 0;
                    return (
                      <TableRow key={gate}>
                        <TableCell className="font-medium">{gate}</TableCell>
                        <TableCell className="tnum text-right text-muted-foreground">
                          {blocked[gate]}
                        </TableCell>
                        <TableCell
                          className={cn("tnum text-right", alone > 0 && "text-warn font-medium")}
                        >
                          {alone}
                        </TableCell>
                        <TableCell className="tnum text-right text-muted-foreground">
                          {share.toFixed(1)}%
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </>
          );
        }}
      </Async>
    </SectionCard>
  );
}

export function RejectionsStatusCard() {
  const query = useRejectionsStatus();
  return (
    <SectionCard title="Registro de rechazos" icon={<ShieldQuestion />}>
      <Async query={query} disabledLabel="Why Not Trade Engine disabled">
        {(data) => <MetricsGrid data={data} columns={2} />}
      </Async>
    </SectionCard>
  );
}

export function RejectionsRecentCard() {
  const query = useRejections(50);
  return (
    <SectionCard title="Rechazos recientes" icon={<ListFilter />} contentClassName="pt-0">
      <Async
        query={query}
        disabledLabel="Why Not Trade Engine disabled"
        isEmpty={(d) => !Array.isArray(d.rejections) || (d.rejections as unknown[]).length === 0}
        emptyLabel="Sin rechazos recientes"
      >
        {(data) => <DictTable rows={(data.rejections as Dict[]) ?? []} />}
      </Async>
    </SectionCard>
  );
}
