"use client";

import { Loader2, MessageSquare, NotebookPen, Send } from "lucide-react";

import { SectionCard } from "@/components/common/section-card";
import { Metric } from "@/components/common/stat";
import { Async } from "@/components/common/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useDiscordStatus, useNotionStatus } from "@/lib/api/hooks";
import { useDiscordTest } from "@/lib/api/mutations";

function YesNo({ value }: { value: boolean }) {
  return (
    <Badge variant="outline" className={value ? "border-bull/40 text-bull" : ""}>
      {value ? "yes" : "no"}
    </Badge>
  );
}

export function DiscordCard() {
  const query = useDiscordStatus();
  const test = useDiscordTest();
  return (
    <SectionCard
      title="Discord"
      icon={<MessageSquare />}
      action={
        <Button size="sm" variant="outline" onClick={() => test.mutate()} disabled={test.isPending}>
          {test.isPending ? <Loader2 className="animate-spin" /> : <Send />}
          Send test
        </Button>
      }
    >
      <Async query={query} disabledLabel="Discord not available">
        {(d) => (
          <div className="space-y-2">
            <div className="grid grid-cols-2 gap-x-6">
              <Metric label="Enabled" value={<YesNo value={d.enabled} />} />
              <Metric label="Configured" value={<YesNo value={d.configured} />} />
              <Metric label="Min level" value={d.min_level} />
              <Metric label="Delivered" value={d.stats.delivered ?? 0} />
              <Metric label="Failed" value={d.stats.failed ?? 0} />
            </div>
            <div className="rounded-md border border-border/60 bg-muted/30 px-2 py-1 font-mono text-xs text-muted-foreground">
              {d.webhook_masked || "— not configured —"}
            </div>
            <p className="text-[11px] text-muted-foreground">
              The full webhook is never exposed. Sending a test posts a real message.
            </p>
          </div>
        )}
      </Async>
    </SectionCard>
  );
}

export function NotionCard() {
  const query = useNotionStatus();
  return (
    <SectionCard title="Notion" icon={<NotebookPen />}>
      <Async query={query} disabledLabel="Notion not available">
        {(d) => (
          <div className="grid grid-cols-2 gap-x-6">
            <Metric label="Enabled" value={<YesNo value={d.enabled} />} />
            <Metric label="Configured" value={<YesNo value={d.configured} />} />
            <Metric label="Database set" value={<YesNo value={d.has_database} />} />
          </div>
        )}
      </Async>
    </SectionCard>
  );
}
