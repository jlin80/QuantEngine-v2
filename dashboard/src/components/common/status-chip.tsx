import { cn } from "@/lib/utils";
import { titleCase } from "@/lib/format";

export type Tone = "bull" | "warn" | "bear" | "muted";

const GOOD = ["healthy", "ok", "running", "up", "connected", "active", "open", "ready"];
const WARN = ["degraded", "warn", "slow", "pending", "reconnect", "connecting", "stale", "idle"];
const BAD = [
  "unhealthy",
  "down",
  "error",
  "frozen",
  "disconnected",
  "dead",
  "closed",
  "failed",
  "stopped",
];

// Order matters: BAD/WARN are checked before GOOD because negative statuses can
// contain a positive substring (e.g. "disconnected" contains "connected").
export function statusTone(value: string): Tone {
  const v = value.toLowerCase();
  if (BAD.some((s) => v.includes(s))) return "bear";
  if (WARN.some((s) => v.includes(s))) return "warn";
  if (GOOD.some((s) => v.includes(s))) return "bull";
  return "muted";
}

const DOT: Record<Tone, string> = {
  bull: "bg-bull",
  warn: "bg-warn",
  bear: "bg-bear",
  muted: "bg-muted-foreground/40",
};

export function StatusChip({ label, value }: { label: string; value: string }) {
  const tone = statusTone(value);
  return (
    <div className="flex items-center justify-between gap-2 rounded-md border border-border/60 px-2 py-1.5 text-xs">
      <span className="truncate text-muted-foreground">{titleCase(label)}</span>
      <span className="flex items-center gap-1.5">
        <span className={cn("size-1.5 rounded-full", DOT[tone])} />
        <span className="capitalize">{value}</span>
      </span>
    </div>
  );
}
