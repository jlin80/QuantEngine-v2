/**
 * Formatting helpers for financial figures. All are null-safe: unknown values
 * render as an em dash rather than "NaN"/"undefined".
 */

const DASH = "—";

export function isNum(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

export function fmtNum(
  v: unknown,
  opts: { decimals?: number; sign?: boolean } = {},
): string {
  if (!isNum(v)) return DASH;
  const { decimals = 2, sign = false } = opts;
  const s = v.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  return sign && v > 0 ? `+${s}` : s;
}

/** Adaptive precision for prices (more decimals for small numbers). */
export function fmtPrice(v: unknown): string {
  if (!isNum(v)) return DASH;
  const abs = Math.abs(v);
  const decimals = abs >= 1000 ? 2 : abs >= 1 ? 3 : abs >= 0.01 ? 5 : 8;
  return v.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

export function fmtMoney(v: unknown, opts: { sign?: boolean } = {}): string {
  if (!isNum(v)) return DASH;
  const s = Math.abs(v).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  const prefix = v < 0 ? "-$" : opts.sign ? "+$" : "$";
  return `${prefix}${s}`;
}

/** Value already expressed as a percentage (e.g. 12.5 -> "12.50%"). */
export function fmtPct(v: unknown, opts: { sign?: boolean; decimals?: number } = {}): string {
  if (!isNum(v)) return DASH;
  const { sign = false, decimals = 2 } = opts;
  const s = v.toFixed(decimals);
  return `${sign && v > 0 ? "+" : ""}${s}%`;
}

/** Fraction (0..1) rendered as a percentage. */
export function fmtRatioPct(v: unknown, decimals = 1): string {
  if (!isNum(v)) return DASH;
  return `${(v * 100).toFixed(decimals)}%`;
}

export function fmtCompact(v: unknown): string {
  if (!isNum(v)) return DASH;
  return v.toLocaleString("en-US", { notation: "compact", maximumFractionDigits: 1 });
}

export function fmtDuration(seconds: unknown): string {
  if (!isNum(seconds)) return DASH;
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
}

export function fmtTime(iso: unknown): string {
  if (typeof iso !== "string") return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return DASH;
  return d.toLocaleTimeString("en-GB", { hour12: false });
}

export function fmtDateTime(iso: unknown): string {
  if (typeof iso !== "string") return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return DASH;
  return d.toLocaleString("en-GB", { hour12: false });
}

/** Relative "time ago" from an ISO timestamp. */
export function fmtAgo(iso: unknown): string {
  if (typeof iso !== "string") return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return DASH;
  const s = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/** Tailwind text class reflecting the sign of a PnL-like number. */
export function pnlClass(v: unknown): string {
  if (!isNum(v) || v === 0) return "text-muted-foreground";
  return v > 0 ? "text-bull" : "text-bear";
}

export function titleCase(s: string): string {
  return s.replace(/[_-]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
