"use client";

import { CalendarDays, Coins, TriangleAlert } from "lucide-react";

import { SectionCard } from "@/components/common/section-card";
import { DictTable, MetricsGrid } from "@/components/common/metrics-grid";
import { Async } from "@/components/common/states";
import { useCostsDaily, useCostsReport } from "@/lib/api/hooks";
import type { Dict } from "@/lib/api/types";

/**
 * Reparto del bruto. El coste oculto viaja como **residuo**: es lo que el
 * reparto no consigue explicar, y esa es justamente su utilidad — un informe
 * que lo escondiera invitaría a leer el desglose como si estuviera completo.
 */
export function CostsReportCard() {
  const query = useCostsReport();
  return (
    <SectionCard title="Reparto del bruto" icon={<Coins />}>
      <Async query={query} disabledLabel="Cost Attribution Engine disabled">
        {(data) => (
          <div className="space-y-3">
            <MetricsGrid data={data} columns={2} skip={["daily", "notes"]} />
            <Notes notes={data.notes} />
          </div>
        )}
      </Async>
    </SectionCard>
  );
}

function Notes({ notes }: { notes: unknown }) {
  const list = Array.isArray(notes) ? (notes as string[]) : [];
  if (list.length === 0) return null;
  return (
    <div className="rounded-md border border-warn/40 bg-warn/5 p-2.5">
      <p className="flex items-center gap-1.5 text-xs font-medium text-warn">
        <TriangleAlert className="size-3.5" />
        Límites de la medición
      </p>
      <ul className="mt-1 space-y-0.5 text-xs text-muted-foreground">
        {list.map((note) => (
          <li key={note}>· {note}</li>
        ))}
      </ul>
    </div>
  );
}

export function CostsDailyCard() {
  const query = useCostsDaily();
  return (
    <SectionCard title="Coste por día" icon={<CalendarDays />} contentClassName="pt-0">
      <Async
        query={query}
        disabledLabel="Cost Attribution Engine disabled"
        isEmpty={(d) => Object.keys((d.daily as Dict) ?? {}).length === 0}
        emptyLabel="Sin días medidos todavía"
      >
        {(data) => {
          const daily = (data.daily as Record<string, Dict>) ?? {};
          const rows = Object.entries(daily).map(([day, breakdown]) => ({
            day,
            ...breakdown,
          }));
          return (
            <div className="space-y-3">
              <DictTable rows={rows} />
              <Notes notes={data.notes} />
            </div>
          );
        }}
      </Async>
    </SectionCard>
  );
}
