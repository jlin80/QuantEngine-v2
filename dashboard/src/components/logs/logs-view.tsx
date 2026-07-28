"use client";

import { useState } from "react";
import { ScrollText } from "lucide-react";

import { SectionCard } from "@/components/common/section-card";
import { Async, EmptyState } from "@/components/common/states";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useLogs } from "@/lib/api/hooks";
import type { LogRecord } from "@/lib/api/types";
import { fmtDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

const LEVELS = ["all", "error", "warning", "info", "debug"] as const;
type Level = (typeof LEVELS)[number];

const LEVEL_STYLES: Record<string, string> = {
  ERROR: "border-bear/50 bg-bear/10 text-bear",
  CRITICAL: "border-bear/50 bg-bear/10 text-bear",
  WARNING: "border-warn/50 bg-warn/10 text-warn",
  INFO: "border-info/40 bg-info/10 text-info",
  DEBUG: "border-border bg-muted/40 text-muted-foreground",
};

function matches(entry: LogRecord, needle: string): boolean {
  if (!needle) return true;
  return (
    entry.message.toLowerCase().includes(needle) ||
    entry.logger.toLowerCase().includes(needle)
  );
}

export function LogsView() {
  const [level, setLevel] = useState<Level>("all");
  const [query, setQuery] = useState("");
  const logs = useLogs(300, level === "all" ? null : level);
  const needle = query.trim().toLowerCase();

  return (
    <SectionCard
      title="Engine logs"
      icon={<ScrollText />}
      contentClassName="pt-2"
      action={
        <div className="flex items-center gap-2">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter…"
            className="h-7 w-36 rounded-md border border-border bg-background px-2 text-xs outline-none focus:border-info"
          />
          <div className="flex items-center gap-1">
            {LEVELS.map((l) => (
              <button
                key={l}
                type="button"
                onClick={() => setLevel(l)}
                className={cn(
                  "rounded-md px-2 py-0.5 text-xs capitalize",
                  level === l
                    ? "bg-muted text-foreground"
                    : "text-muted-foreground hover:bg-muted/50",
                )}
              >
                {l}
              </button>
            ))}
          </div>
        </div>
      }
    >
      <Async
        query={logs}
        disabledLabel="Logs not enabled"
        isEmpty={(d) => d.logs.length === 0}
        emptyLabel="No log entries"
      >
        {(data) => {
          const shown = data.logs.filter((l) => matches(l, needle));
          if (shown.length === 0) {
            return <EmptyState label="No matches" detail={`Nothing matching “${query}”`} />;
          }
          return (
            <ScrollArea className="h-[calc(100vh-16rem)]">
              <div className="space-y-1 pr-3 font-mono text-xs">
                {shown.map((entry, i) => (
                  <div
                    key={`${entry.timestamp}-${i}`}
                    className="flex items-start gap-2 rounded-md border border-border/60 px-2 py-1"
                  >
                    <span className="shrink-0 text-muted-foreground">
                      {fmtDateTime(entry.timestamp)}
                    </span>
                    <span
                      className={cn(
                        "shrink-0 rounded border px-1 uppercase",
                        LEVEL_STYLES[entry.level.toUpperCase()] ?? LEVEL_STYLES.DEBUG,
                      )}
                    >
                      {entry.level}
                    </span>
                    <span className="shrink-0 text-muted-foreground">{entry.logger}</span>
                    <span className="whitespace-pre-wrap break-all">{entry.message}</span>
                  </div>
                ))}
              </div>
            </ScrollArea>
          );
        }}
      </Async>
    </SectionCard>
  );
}
