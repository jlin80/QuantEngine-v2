"use client";

import {
  Archive,
  GitBranch,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Wrench,
} from "lucide-react";

import { SectionCard } from "@/components/common/section-card";
import { Async } from "@/components/common/states";
import { StatTile } from "@/components/common/stat";
import { StatusChip } from "@/components/common/status-chip";
import { Button } from "@/components/ui/button";
import {
  useBackups,
  useProductionStatus,
  useSecurityReport,
  useUpdates,
} from "@/lib/api/hooks";
import {
  useBackupCreate,
  useBackupRestore,
  useImprovementAnalyze,
  useMaintenanceEnter,
  useMaintenanceExit,
  useNotionSync,
  useOperationalReport,
} from "@/lib/api/mutations";

type Dict = Record<string, unknown>;

function rec(value: unknown): Dict {
  return value && typeof value === "object" ? (value as Dict) : {};
}
function arr(value: unknown): Dict[] {
  return Array.isArray(value) ? (value as Dict[]) : [];
}
function str(value: unknown, fallback = "—"): string {
  if (value === null || value === undefined) return fallback;
  return String(value);
}
function bool(value: unknown): boolean {
  return value === true;
}

// ---------------------------------------------------------------- status

export function ProductionStatusCard() {
  const query = useProductionStatus();
  return (
    <SectionCard title="Producción" icon={<ShieldCheck />}>
      <Async query={query} disabledLabel="Production layer disabled">
        {(data: Dict) => {
          const mode = rec(data.mode);
          const safeMode = rec(data.safe_mode);
          const killSwitch = rec(data.kill_switch);
          const gate = rec(data.live_gate);
          const maintenance = rec(data.maintenance);
          const failover = rec(data.failover);
          return (
            <div className="space-y-3">
              <div className="grid gap-2 sm:grid-cols-3">
                <StatTile
                  label="Modo resuelto"
                  value={str(mode.mode, "paper")}
                  valueClassName={mode.mode === "live" ? "text-bear" : "text-bull"}
                />
                <StatTile
                  label="Live Gate"
                  value={bool(gate.approved) ? "aprobado" : "bloqueado"}
                  sub={gate.failed !== undefined ? `${str(gate.failed)} criterios sin cumplir` : undefined}
                  valueClassName={bool(gate.approved) ? "text-bull" : "text-muted-foreground"}
                />
                <StatTile
                  label="Nodo"
                  value={bool(failover.is_primary) || failover.is_primary === undefined ? "primario" : "standby"}
                  sub={bool(failover.enabled) ? str(failover.node_id) : "HA off"}
                />
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                <StatusChip label="Safe Mode" value={bool(safeMode.active) ? "active" : "off"} />
                <StatusChip
                  label="Kill Switch"
                  value={bool(killSwitch.active) ? "active" : "ready"}
                />
                <StatusChip
                  label="Mantenimiento"
                  value={bool(maintenance.active) ? "active" : "idle"}
                />
                <StatusChip label="Estado" value={bool(data.enabled) ? "running" : "off"} />
              </div>
            </div>
          );
        }}
      </Async>
    </SectionCard>
  );
}

// ---------------------------------------------------------------- security

export function SecurityCard() {
  const query = useSecurityReport();
  const syncNotion = useNotionSync();
  return (
    <SectionCard
      title="Seguridad"
      icon={<ShieldCheck />}
      action={
        <Button
          size="sm"
          variant="outline"
          onClick={() => syncNotion.mutate()}
          disabled={syncNotion.isPending}
        >
          Sync Notion
        </Button>
      }
    >
      <Async query={query} disabledLabel="Security disabled">
        {(data: Dict) => {
          const issues = arr(data.issues);
          const secrets = rec(data.secrets);
          const due = arr(secrets.due).length || (Array.isArray(secrets.due) ? 0 : 0);
          const dueList = Array.isArray(secrets.due) ? (secrets.due as string[]) : [];
          return (
            <div className="space-y-3">
              <div className="grid gap-2 sm:grid-cols-3">
                <StatTile
                  label="Config"
                  value={bool(data.ok) ? "OK" : "revisar"}
                  valueClassName={bool(data.ok) ? "text-bull" : "text-warn"}
                />
                <StatTile label="Errores" value={str(data.errors, "0")} />
                <StatTile label="Avisos" value={str(data.warnings, "0")} />
              </div>
              {dueList.length > 0 ? (
                <p className="text-xs text-warn">
                  Secretos por rotar: {dueList.join(", ")}
                </p>
              ) : (
                <p className="text-xs text-muted-foreground">Rotación de secretos al día ({due} pendientes).</p>
              )}
              <div className="space-y-1">
                {issues.slice(0, 6).map((issue, i) => (
                  <div
                    key={i}
                    className="flex items-start justify-between gap-2 rounded-md border border-border/60 px-2 py-1.5 text-xs"
                  >
                    <span className="text-muted-foreground">{str(issue.field)}</span>
                    <span className={issue.severity === "error" ? "text-bear" : "text-warn"}>
                      {str(issue.message)}
                    </span>
                  </div>
                ))}
                {issues.length === 0 ? (
                  <p className="text-xs text-muted-foreground">Sin hallazgos de configuración.</p>
                ) : null}
              </div>
            </div>
          );
        }}
      </Async>
    </SectionCard>
  );
}

// ---------------------------------------------------------------- backups

export function BackupsCard() {
  const query = useBackups();
  const create = useBackupCreate();
  const restore = useBackupRestore();
  return (
    <SectionCard
      title="Backups"
      icon={<Archive />}
      action={
        <Button size="sm" onClick={() => create.mutate()} disabled={create.isPending}>
          {create.isPending ? "Creando…" : "Crear backup"}
        </Button>
      }
    >
      <Async query={query} disabledLabel="Backups disabled">
        {(data: Dict) => {
          const backups = arr(data.backups);
          if (backups.length === 0) {
            return <p className="text-xs text-muted-foreground">No hay backups todavía.</p>;
          }
          return (
            <div className="space-y-1">
              {backups.slice(0, 8).map((b) => (
                <div
                  key={str(b.backup_id)}
                  className="flex items-center justify-between gap-2 rounded-md border border-border/60 px-2 py-1.5 text-xs"
                >
                  <div className="min-w-0">
                    <p className="truncate font-medium">{str(b.backup_id)}</p>
                    <p className="tnum text-muted-foreground">
                      {str(b.kind)} · {Math.round(Number(b.size_bytes ?? 0) / 1024)} KB
                    </p>
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => restore.mutate(str(b.backup_id))}
                    disabled={restore.isPending}
                  >
                    Restaurar
                  </Button>
                </div>
              ))}
            </div>
          );
        }}
      </Async>
    </SectionCard>
  );
}

// ---------------------------------------------------------------- operations

export function OperationsCard() {
  const updates = useUpdates();
  const enterMaint = useMaintenanceEnter();
  const exitMaint = useMaintenanceExit();
  const analyze = useImprovementAnalyze();
  const sendReport = useOperationalReport();
  return (
    <SectionCard title="Operación" icon={<Wrench />}>
      <div className="space-y-3">
        <Async query={updates} disabledLabel="Updates disabled">
          {(data: Dict) => (
            <div className="grid gap-2 sm:grid-cols-3">
              <StatTile label="Versión" value={str(data.current)} icon={<GitBranch />} />
              <StatTile
                label="Actualización"
                value={bool(data.available) ? "disponible" : "al día"}
                valueClassName={bool(data.available) ? "text-warn" : "text-bull"}
              />
              <StatTile label="Canal" value={str(data.channel, "stable")} />
            </div>
          )}
        </Async>
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => enterMaint.mutate("mantenimiento manual")}
            disabled={enterMaint.isPending}
          >
            <Wrench className="size-3.5" /> Entrar mantenimiento
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => exitMaint.mutate()}
            disabled={exitMaint.isPending}
          >
            Salir
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => analyze.mutate()}
            disabled={analyze.isPending}
          >
            <Sparkles className="size-3.5" /> Analizar mejoras
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => sendReport.mutate("daily")}
            disabled={sendReport.isPending}
          >
            <RefreshCw className="size-3.5" /> Enviar reporte
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          La observabilidad completa (métricas, alertas) vive en Grafana/Prometheus. Live
          trading permanece deshabilitado: ninguna acción de este panel puede habilitarlo.
        </p>
      </div>
    </SectionCard>
  );
}
