import { cn } from "@/lib/utils";

/** Compact KPI tile: label on top, big tabular value, optional sub-line. */
export function StatTile({
  label,
  value,
  sub,
  valueClassName,
  className,
  icon,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  valueClassName?: string;
  className?: string;
  icon?: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex flex-col gap-1 rounded-lg border border-border/70 bg-card/40 px-3 py-2.5",
        className,
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
          {label}
        </span>
        {icon ? <span className="text-muted-foreground [&>svg]:size-3.5">{icon}</span> : null}
      </div>
      <span className={cn("tnum text-lg leading-tight font-semibold", valueClassName)}>
        {value}
      </span>
      {sub ? <span className="tnum text-xs text-muted-foreground">{sub}</span> : null}
    </div>
  );
}

/** Inline label:value row used inside cards. */
export function Metric({
  label,
  value,
  valueClassName,
}: {
  label: string;
  value: React.ReactNode;
  valueClassName?: string;
}) {
  return (
    <div className="flex items-center justify-between gap-3 py-1 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className={cn("tnum font-medium", valueClassName)}>{value}</span>
    </div>
  );
}
