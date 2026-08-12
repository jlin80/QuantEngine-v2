"use client";

import { Infinity as InfinityIcon, ShieldAlert, ShieldCheck } from "lucide-react";

import { SectionCard } from "@/components/common/section-card";
import { Metric } from "@/components/common/stat";
import { Async } from "@/components/common/states";
import { useRisk } from "@/lib/api/hooks";
import { useConfigPatch } from "@/lib/api/mutations";
import { fmtMoney, fmtPct, pnlClass } from "@/lib/format";
import { cn } from "@/lib/utils";

function Guard({
  label,
  active,
  reason,
}: {
  label: string;
  active: boolean;
  reason: string;
}) {
  return (
    <div
      className={cn(
        "rounded-md border px-2.5 py-2",
        active ? "border-bear/50 bg-bear/10" : "border-bull/30 bg-bull/5",
      )}
    >
      <div className="flex items-center gap-1.5 text-xs font-medium">
        {active ? (
          <ShieldAlert className="text-bear size-3.5" />
        ) : (
          <ShieldCheck className="text-bull size-3.5" />
        )}
        <span>{label}</span>
      </div>
      <p className="mt-0.5 truncate text-xs">
        {active ? (
          <span className="text-bear">{reason || "Triggered"}</span>
        ) : (
          <span className="text-muted-foreground">Nominal</span>
        )}
      </p>
    </div>
  );
}

/**
 * Operator override for every drawdown-based halt. It patches the same
 * whitelisted setting the config panel exposes, but lives here because this is
 * where the operator sees the kill switch fire — having to hunt for it in the
 * settings page while positions are being flattened is exactly the wrong flow.
 */
function IgnoreDrawdownToggle({ active }: { active: boolean }) {
  const patch = useConfigPatch();
  const pending = patch.isPending;
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-3 rounded-md border px-2.5 py-2",
        active ? "border-warn/50 bg-warn/10" : "border-border",
      )}
    >
      <div className="min-w-0">
        <p className="flex items-center gap-1.5 text-xs font-medium">
          <InfinityIcon
            className={cn("size-3.5", active ? "text-warn" : "text-muted-foreground")}
          />
          Ignore drawdown limits
        </p>
        <p className="mt-0.5 truncate text-xs text-muted-foreground">
          {active
            ? "El bot no se detiene por drawdown (kill switch, safe mode y filtro diario)"
            : "Las paradas por drawdown están activas"}
        </p>
      </div>
      <button
        type="button"
        aria-pressed={active}
        aria-label="Ignore drawdown limits"
        disabled={pending}
        onClick={() => patch.mutate({ "execution.risk.ignore_drawdown_limits": !active })}
        className={cn(
          "h-6 w-11 shrink-0 rounded-full border transition-colors disabled:opacity-50",
          active ? "border-warn/50 bg-warn/30" : "border-border bg-muted",
        )}
      >
        <span
          className={cn(
            "block size-4 rounded-full bg-foreground/80 transition-transform",
            active ? "translate-x-6" : "translate-x-1",
          )}
        />
      </button>
    </div>
  );
}

export function RiskCard() {
  const query = useRisk();
  return (
    <SectionCard title="Risk manager" icon={<ShieldAlert />}>
      <Async query={query} disabledLabel="Execution engine disabled">
        {(r) => (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <Guard label="Kill switch" active={r.kill_switch} reason={r.kill_reason} />
              <Guard label="Circuit breaker" active={r.circuit_breaker} reason={r.circuit_reason} />
            </div>
            <IgnoreDrawdownToggle active={r.ignore_drawdown_limits} />
            <div className="grid grid-cols-2 gap-x-6">
              <Metric label="Consecutive losses" value={r.consecutive_losses} />
              <Metric
                label="Drawdown"
                value={fmtPct(r.drawdown_pct)}
                valueClassName={r.drawdown_pct > 0 ? "text-bear" : undefined}
              />
              <Metric
                label="Realized today"
                value={fmtMoney(r.realized_today, { sign: true })}
                valueClassName={pnlClass(r.realized_today)}
              />
            </div>
            <div className="border-t pt-2">
              <p className="mb-1 text-[11px] tracking-wide text-muted-foreground uppercase">
                Limits
              </p>
              <div className="grid grid-cols-2 gap-x-6">
                <Metric label="Risk / trade" value={fmtPct(r.limits.max_risk_per_trade_pct)} />
                <Metric label="Daily loss" value={fmtPct(r.limits.max_daily_loss_pct)} />
                <Metric label="Max positions" value={r.limits.max_open_positions} />
                <Metric label="Max exposure" value={fmtPct(r.limits.max_exposure_pct)} />
                <Metric label="Kill @ DD" value={fmtPct(r.limits.kill_switch_drawdown_pct)} />
              </div>
            </div>
          </div>
        )}
      </Async>
    </SectionCard>
  );
}
