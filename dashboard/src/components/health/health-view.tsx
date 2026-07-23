"use client";

import { AlertTriangle, HeartPulse, Radio, Server } from "lucide-react";

import { MetricsGrid } from "@/components/common/metrics-grid";
import { SectionCard } from "@/components/common/section-card";
import { StatTile } from "@/components/common/stat";
import { StatusChip } from "@/components/common/status-chip";
import { Async, EmptyState } from "@/components/common/states";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useMarketStatus, useSystemStatus } from "@/lib/api/hooks";
import { useRealtimeStore } from "@/lib/store/realtime";
import { fmtDuration, fmtNum, fmtPct } from "@/lib/format";
import { cn } from "@/lib/utils";

function statusBadge(status: string): string {
  if (status === "healthy") return "border-bull/40 text-bull";
  if (status === "degraded") return "border-warn/40 text-warn";
  return "border-bear/40 text-bear";
}

export function VitalsCard() {
  const query = useSystemStatus();
  return (
    <SectionCard
      title="System vitals"
      icon={<HeartPulse />}
      action={
        query.data ? (
          <Badge variant="outline" className={cn("capitalize", statusBadge(query.data.status))}>
            {query.data.status}
          </Badge>
        ) : null
      }
    >
      <Async query={query} disabledLabel="Health monitor unavailable">
        {(h) => (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <StatTile label="CPU" value={fmtPct(h.cpu_percent)} />
            <StatTile label="Memory" value={fmtPct(h.memory_percent)} sub={`${fmtNum(h.memory_used_mb, { decimals: 0 })} MB`} />
            <StatTile label="Disk" value={fmtPct(h.disk_percent)} />
            <StatTile label="Loop lag" value={`${fmtNum(h.event_loop_lag_ms, { decimals: 2 })} ms`} />
            <StatTile label="Uptime" value={fmtDuration(h.uptime_seconds)} />
            <StatTile
              label="Errors"
              value={h.recent_errors?.length ?? 0}
              valueClassName={(h.recent_errors?.length ?? 0) > 0 ? "text-warn" : undefined}
            />
          </div>
        )}
      </Async>
    </SectionCard>
  );
}

export function ComponentsCard() {
  const query = useSystemStatus();
  const wsStatus = useRealtimeStore((s) => s.status);
  return (
    <SectionCard title="Components" icon={<Server />}>
      <Async query={query} disabledLabel="Health monitor unavailable">
        {(h) => (
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            <StatusChip label="WebSocket" value={wsStatus === "open" ? "connected" : wsStatus} />
            <StatusChip
              label="Event bus"
              value={h.event_bus && Object.keys(h.event_bus).length > 0 ? "active" : "idle"}
            />
            {Object.entries(h.components ?? {}).map(([name, value]) => (
              <StatusChip key={name} label={name} value={value} />
            ))}
          </div>
        )}
      </Async>
    </SectionCard>
  );
}

export function RecentErrorsCard() {
  const query = useSystemStatus();
  return (
    <SectionCard title="Recent errors" icon={<AlertTriangle />} contentClassName="pt-2">
      <Async query={query} disabledLabel="Health monitor unavailable">
        {(h) =>
          (h.recent_errors?.length ?? 0) === 0 ? (
            <EmptyState label="No recent errors" detail="The error buffer is empty." />
          ) : (
            <ScrollArea className="h-64">
              <ul className="pr-3 font-mono text-xs">
                {h.recent_errors.map((e, i) => (
                  <li key={i} className="flex gap-2 px-1 py-0.5">
                    <span className="text-bear w-14 shrink-0 uppercase">{e.level}</span>
                    <span className="shrink-0 text-muted-foreground">{e.logger}</span>
                    <span className="break-all">{e.message}</span>
                  </li>
                ))}
              </ul>
            </ScrollArea>
          )
        }
      </Async>
    </SectionCard>
  );
}

export function FeedCard() {
  const query = useMarketStatus();
  return (
    <SectionCard title="Data feed" icon={<Radio />}>
      <Async query={query} disabledLabel="Data engine disabled">
        {(d) => <MetricsGrid data={d} columns={2} />}
      </Async>
    </SectionCard>
  );
}
