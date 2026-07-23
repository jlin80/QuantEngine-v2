"use client";

import { motion } from "framer-motion";
import { Bell, Radio } from "lucide-react";

import { SectionCard } from "@/components/common/section-card";
import { EmptyState } from "@/components/common/states";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useRealtimeStore } from "@/lib/store/realtime";
import { fmtAgo } from "@/lib/format";
import { eventSymbol } from "@/lib/ws/events";
import { cn } from "@/lib/utils";

export function RecentEventsCard() {
  const events = useRealtimeStore((s) => s.events);

  return (
    <SectionCard title="Event stream" icon={<Radio />} contentClassName="pt-2">
      {events.length === 0 ? (
        <EmptyState
          label="Waiting for events"
          detail="Live bus events appear here once the engine is streaming."
        />
      ) : (
        <ScrollArea className="h-64">
          <ul className="space-y-1 pr-3">
            {events.slice(0, 60).map((e) => {
              const symbol = eventSymbol(e);
              return (
                <motion.li
                  key={e.event_id}
                  initial={{ opacity: 0, y: -4 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-muted/50"
                >
                  <span className="bg-muted-foreground/40 size-1.5 shrink-0 rounded-full" />
                  <span className="font-medium">{e.event}</span>
                  {symbol && <span className="text-muted-foreground">{symbol}</span>}
                  <span className="ml-auto shrink-0 text-muted-foreground/70">
                    {fmtAgo(e.occurred_at)}
                  </span>
                </motion.li>
              );
            })}
          </ul>
        </ScrollArea>
      )}
    </SectionCard>
  );
}

const SEVERITY_STYLES: Record<string, string> = {
  critical: "border-bear/50 bg-bear/10 text-bear",
  warning: "border-warn/50 bg-warn/10 text-warn",
  info: "border-info/40 bg-info/10 text-info",
};

export function AlertsCard() {
  const alerts = useRealtimeStore((s) => s.alerts);
  const clear = useRealtimeStore((s) => s.clearAlerts);

  return (
    <SectionCard
      title="Alerts"
      icon={<Bell />}
      contentClassName="pt-2"
      action={
        alerts.length > 0 ? (
          <button
            type="button"
            onClick={clear}
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            Clear
          </button>
        ) : null
      }
    >
      {alerts.length === 0 ? (
        <EmptyState label="No alerts" detail="Risk, connection and drift events surface here." />
      ) : (
        <ScrollArea className="h-64">
          <ul className="space-y-1.5 pr-3">
            {alerts.map((a) => (
              <motion.li
                key={a.id}
                initial={{ opacity: 0, x: -6 }}
                animate={{ opacity: 1, x: 0 }}
                className={cn(
                  "rounded-md border px-2.5 py-1.5 text-xs",
                  SEVERITY_STYLES[a.severity] ?? "border-border",
                )}
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
