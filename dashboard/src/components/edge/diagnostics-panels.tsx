"use client";

import {
  Award,
  BarChart3,
  Layers,
  LayoutGrid,
  Loader2,
  Radar,
  Radio,
  RefreshCw,
  Server,
  Workflow,
} from "lucide-react";
import type { UseMutationResult, UseQueryResult } from "@tanstack/react-query";

import { SectionCard } from "@/components/common/section-card";
import { DictTable, MetricsGrid } from "@/components/common/metrics-grid";
import { Async } from "@/components/common/states";
import { Button } from "@/components/ui/button";
import type { ApiError } from "@/lib/api/client";
import {
  useBenchmarkReport,
  useCorrelationReport,
  useExecutionOptimizerPlan,
  useForecastStatus,
  useMetaRisk,
  useMicrostructureStatus,
  usePortfolioReport,
  useQualityStatus,
} from "@/lib/api/hooks";
import {
  useCorrelationCycle,
  useForecastCycle,
  useMetaRiskMeasure,
  useQualityMeasure,
} from "@/lib/api/mutations";
import type { Dict } from "@/lib/api/types";

/**
 * Tarjeta genérica para los bloques que publican un dict de estado. Se apoya en
 * `MetricsGrid`/`DictTable` en vez de tipar cada forma a mano: el contrato de
 * estos endpoints todavía se mueve, y una pantalla que se rompe al añadir un
 * campo es peor que una que lo muestra sin formato.
 *
 * `note` no es decorativo: varios de estos bloques declaran ausencia de
 * medición (`observable: false`, `None`) en vez de escribir un 0, y sin el
 * recordatorio a la vista un `—` se lee como "cero" igual de fácil.
 */
function DiagnosticCard({
  title,
  icon,
  query,
  disabledLabel,
  note,
  action,
  rowsKey,
  columns = 2,
}: {
  title: string;
  icon: React.ReactNode;
  query: UseQueryResult<Dict, ApiError>;
  disabledLabel: string;
  note?: string;
  action?: React.ReactNode;
  rowsKey?: string;
  columns?: 1 | 2 | 3;
}) {
  return (
    <SectionCard title={title} icon={icon} action={action}>
      <Async query={query} disabledLabel={disabledLabel}>
        {(data) => {
          const rows = rowsKey && Array.isArray(data[rowsKey]) ? (data[rowsKey] as Dict[]) : null;
          return (
            <div className="space-y-3">
              {note && <p className="text-xs text-muted-foreground">{note}</p>}
              <MetricsGrid data={data} columns={columns} skip={rowsKey ? [rowsKey] : []} />
              {rows && rows.length > 0 && <DictTable rows={rows} />}
            </div>
          );
        }}
      </Async>
    </SectionCard>
  );
}

function CycleButton({
  mutation,
  label = "Medir",
}: {
  mutation: UseMutationResult<Dict, ApiError, void>;
  label?: string;
}) {
  return (
    <Button
      size="xs"
      variant="ghost"
      onClick={() => mutation.mutate()}
      disabled={mutation.isPending}
    >
      {mutation.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />}
      {label}
    </Button>
  );
}

export function DataQualityCard() {
  return (
    <DiagnosticCard
      title="Calidad del dato"
      icon={<Server />}
      query={useQualityStatus()}
      disabledLabel="Data Quality Engine disabled"
      note="Reduce exposición, nunca apaga. Un motor ciego no se esconde detrás de la media de las demás señales."
      action={<CycleButton mutation={useQualityMeasure()} />}
      rowsKey="signals"
    />
  );
}

export function MetaRiskCard() {
  return (
    <DiagnosticCard
      title="Meta riesgo"
      icon={<Layers />}
      query={useMetaRisk()}
      disabledLabel="Meta Risk Engine disabled"
      note="Compone con la calidad del dato por producto, no por mínimo."
      action={<CycleButton mutation={useMetaRiskMeasure()} />}
    />
  );
}

export function RegimeForecastCard() {
  return (
    <DiagnosticCard
      title="Pronóstico de régimen"
      icon={<Radar />}
      query={useForecastStatus()}
      disabledLabel="Regime Forecast disabled"
      note="Brier contra el pronóstico trivial. Un skill negativo se publica igual: es la única forma de saber que el modelo no aporta."
      action={<CycleButton mutation={useForecastCycle()} />}
    />
  );
}

export function CorrelationCard() {
  return (
    <DiagnosticCard
      title="Correlación"
      icon={<Workflow />}
      query={useCorrelationReport()}
      disabledLabel="Correlation Engine disabled"
      note="Sin fingir un test ADF: se reporta la vida media del residuo, que es lo accionable."
      action={<CycleButton mutation={useCorrelationCycle()} />}
      rowsKey="pairs"
    />
  );
}

export function MicrostructureCard() {
  return (
    <DiagnosticCard
      title="Microestructura"
      icon={<Radio />}
      query={useMicrostructureStatus()}
      disabledLabel="Microstructure Engine disabled"
      note="MT5 no publica libro de órdenes: este bloque declara ausencia de medición en vez de escribir ceros. Está esperando una fuente de datos, no un arreglo."
    />
  );
}

export function PortfolioIntelligenceCard() {
  return (
    <DiagnosticCard
      title="Portfolio Intelligence"
      icon={<BarChart3 />}
      query={usePortfolioReport()}
      disabledLabel="Portfolio Intelligence disabled"
      note="Cada contribución con su muestra: una racha no es mérito."
      rowsKey="contributions"
    />
  );
}

export function BenchmarkCard() {
  return (
    <DiagnosticCard
      title="Live Shadow Benchmark"
      icon={<Award />}
      query={useBenchmarkReport()}
      disabledLabel="Shadow Benchmark disabled"
      note="Sin carril live el gap es línea base, no medición."
    />
  );
}

export function ExecutionOptimizerCard() {
  return (
    <DiagnosticCard
      title="Execution Optimizer"
      icon={<LayoutGrid />}
      query={useExecutionOptimizerPlan()}
      disabledLabel="Execution Optimizer disabled"
      note="Cotiza pero no rutea: el Execution Engine sigue mandando MARKET. El coste de NO ejecutar entra como término de primera clase."
    />
  );
}
