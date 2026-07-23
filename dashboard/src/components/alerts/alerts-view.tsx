"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { Bell, ScrollText } from "lucide-react";

import { SectionCard } from "@/components/common/section-card";
import { Async, EmptyState } from "@/components/common/states";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useAudit } from "@/lib/api/hooks";
import { useRealtimeStore } from "@/lib/store/realtime";
import type { AlertSeverity } from "@/lib/ws/events";
import { fmtAgo, fmtDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

const SEVERITY_STYLES: Record<string, string> = {
  critical: "border-bear/50 bg-bear/10 text-bear",
  warning: "border-warn/50 bg-warn/10 text-warn",
  info: "border-info/40 bg-info/10 text-info",
};

const FILTERS: (AlertSeverity | "all")[] = ["all", "critical", "warning", "info"];

export function AlertsFeed() {
  const alerts = useRealtimeStore((s) => s.alerts);
  const clear = useRealtimeStore((s) => s.clearAlerts);
  const [filter, setFilter] = useState<AlertSeverity | "all">("all");
  const shown = filter === "all" ? alerts : alerts.filter((a) => a.severity === filter);

  return (
    <SectionCard
      title="Alerts"
      icon={<Bell />}
      contentClassName="pt-2"
      action={
        <div className="flex items-center gap-1">
          {FILTERS.map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => setFilter(f)}
              className={cn(
                "rounded-md px-2 py-0.5 text-xs capitalize",
                filter === f ? "bg-muted text-foreground" : "text-muted-foreground hover:bg-muted/50",
              )}
            >
              {f}
            </button>
          ))}
          {alerts.length > 0 && (
            <button
              type="button"
              onClick={clear}
              className="ml-1 text-xs text-muted-foreground hover:text-foreground"
            >
              Clear
            </button>
          )}
        </div>
      }
    >
      {shown.length === 0 ? (
        <EmptyState label="No alerts" detail="Risk, drift, connection and latency events surface here." />
      ) : (
        <ScrollArea className="h-[420px]">
          <ul className="space-y-1.5 pr-3">
            {shown.map((a) => (
              <motion.li
                key={a.id}
                initial={{ opacity: 0, x: -6 }}
                animate={{ opacity: 1, x: 0 }}
                className={cn("rounded-md border px-2.5 py-1.5 text-xs", SEVERITY_STYLES[a.severity])}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{a.event}</span>
                  <span className="opacity-70">{fmtAgo(a.at)}</span>
                </div>
                <p className="mt-0.5 truncate text-foreground/80">{a.message}</p>
              </motion.li>
            ))}
          </ul>
        </ScrollArea>
      )}
    </SectionCard>
  );
}

export function AuditTrail() {
  const query = useAudit(150);
  return (
    <SectionCard title="Audit trail" icon={<ScrollText />} contentClassName="pt-2">
      <Async
        query={query}
        disabledLabel="Audit log unavailable"
        isEmpty={(d) => d.entries.length === 0}
        emptyLabel="No audited actions yet"
        emptyDetail="Config changes and commands are recorded here."
      >
        {(d) => (
          <ScrollArea className="h-[420px]">
            <ul className="space-y-1 pr-3 text-xs">
              {d.entries.map((e, i) => (
                <li key={i} className="flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-muted/50">
                  <span className="bg-info/60 size-1.5 shrink-0 rounded-full" />
                  <span className="font-medium">{e.action}</span>
                  {e.target && <span className="text-muted-foreground">{e.target}</span>}
                  <span className="text-muted-foreground/70">· {e.actor}</span>
                  <span className="ml-auto shrink-0 text-muted-foreground/70">
                    {fmtDateTime(e.timestamp)}
                  </span>
                </li>
              ))}
            </ul>
          </ScrollArea>
        )}
      </Async>
    </SectionCard>
  );
}
