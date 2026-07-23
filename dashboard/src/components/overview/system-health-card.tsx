"use client";

import { Activity } from "lucide-react";

import { SectionCard } from "@/components/common/section-card";
import { Metric } from "@/components/common/stat";
import { StatusChip } from "@/components/common/status-chip";
import { Async } from "@/components/common/states";
import { Badge } from "@/components/ui/badge";
import { useSystemStatus } from "@/lib/api/hooks";
import { useRealtimeStore } from "@/lib/store/realtime";
import { fmtDuration, fmtNum, fmtPct } from "@/lib/format";
import { cn } from "@/lib/utils";

function statusBadgeClass(status: string): string {
  if (status === "healthy") return "border-bull/40 text-bull";
  if (status === "degraded") return "border-warn/40 text-warn";
  return "border-bear/40 text-bear";
}

export function SystemHealthCard() {
  const query = useSystemStatus();
  const wsStatus = useRealtimeStore((s) => s.status);

  return (
    <SectionCard
      title="System health"
      icon={<Activity />}
      action={
        query.data ? (
          <Badge variant="outline" className={cn("capitalize", statusBadgeClass(query.data.status))}>
            {query.data.status}
          </Badge>
        ) : null
      }
    >
      <Async query={query} disabledLabel="Health monitor unavailable">
        {(h) => {
          const errorCount = h.recent_errors?.length ?? 0;
          const busActive = h.event_bus && Object.keys(h.event_bus).length > 0;
          return (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-x-6">
                <Metric label="CPU" value={fmtPct(h.cpu_percent)} />
                <Metric label="Memory" value={fmtPct(h.memory_percent)} />
                <Metric label="Disk" value={fmtPct(h.disk_percent)} />
                <Metric label="Loop lag" value={`${fmtNum(h.event_loop_lag_ms)} ms`} />
                <Metric label="Uptime" value={fmtDuration(h.uptime_seconds)} />
                <Metric
                  label="Recent errors"
                  value={errorCount}
                  valueClassName={errorCount > 0 ? "text-warn" : undefined}
                />
              </div>
              <div className="grid grid-cols-2 gap-2">
                <StatusChip
                  label="WebSocket"
                  value={wsStatus === "open" ? "connected" : wsStatus}
                />
                <StatusChip label="Event bus" value={busActive ? "active" : "idle"} />
                {Object.entries(h.components ?? {}).map(([name, value]) => (
                  <StatusChip key={name} label={name} value={value} />
                ))}
              </div>
            </div>
          );
        }}
      </Async>
    </SectionCard>
  );
}
