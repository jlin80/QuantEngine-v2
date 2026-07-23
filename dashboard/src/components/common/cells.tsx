import { cn } from "@/lib/utils";

export function SideBadge({ side }: { side: string }) {
  const s = side.toLowerCase();
  const buy = s === "buy" || s === "long";
  return (
    <span
      className={cn(
        "inline-block rounded px-1.5 py-0.5 text-[11px] font-semibold uppercase",
        buy ? "bg-bull/15 text-bull" : "bg-bear/15 text-bear",
      )}
    >
      {side}
    </span>
  );
}

export function Flag({ label, tone }: { label: string; tone: "bull" | "info" | "muted" }) {
  const cls =
    tone === "bull"
      ? "border-bull/40 text-bull"
      : tone === "info"
        ? "border-info/40 text-info"
        : "border-border text-muted-foreground";
  return (
    <span className={cn("rounded-sm border px-1 py-px text-[9px] tracking-wide uppercase", cls)}>
      {label}
    </span>
  );
}
