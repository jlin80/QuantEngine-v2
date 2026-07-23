import { cn } from "@/lib/utils";

/** Minimal inline sparkline (SVG polyline), auto-colored by first→last trend. */
export function Sparkline({
  data,
  width = 96,
  height = 28,
  className,
  color,
}: {
  data: number[];
  width?: number;
  height?: number;
  className?: string;
  color?: string;
}) {
  const points = data.filter((v) => Number.isFinite(v));
  if (points.length < 2) {
    return <div className={cn("h-7 w-24", className)} aria-hidden />;
  }

  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const stepX = width / (points.length - 1);

  const path = points
    .map((v, i) => {
      const x = i * stepX;
      const y = height - ((v - min) / span) * height;
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  const up = points[points.length - 1] >= points[0];
  const stroke = color ?? (up ? "var(--bull)" : "var(--bear)");

  return (
    <svg
      className={cn("overflow-visible", className)}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      aria-hidden
    >
      <path d={path} fill="none" stroke={stroke} strokeWidth={1.5} strokeLinejoin="round" />
    </svg>
  );
}
