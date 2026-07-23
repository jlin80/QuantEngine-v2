import { Metric } from "@/components/common/stat";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { fmtDateTime, fmtNum, titleCase } from "@/lib/format";
import { cn } from "@/lib/utils";

const ISO_RE = /^\d{4}-\d\d-\d\dT/;

export function renderScalar(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "number") {
    return Number.isInteger(v) ? fmtNum(v, { decimals: 0 }) : fmtNum(v, { decimals: 4 });
  }
  if (typeof v === "string") return ISO_RE.test(v) ? fmtDateTime(v) : v;
  if (Array.isArray(v)) return `[${v.length}]`;
  return JSON.stringify(v);
}

function isScalar(v: unknown): boolean {
  return v === null || ["string", "number", "boolean"].includes(typeof v);
}

/** Render a dict's scalar fields as a grid of label:value metrics. */
export function MetricsGrid({
  data,
  columns = 2,
  skip = [],
}: {
  data: Record<string, unknown>;
  columns?: 1 | 2 | 3;
  skip?: string[];
}) {
  const entries = Object.entries(data).filter(([k, v]) => !skip.includes(k) && isScalar(v));
  if (entries.length === 0) {
    return <p className="text-sm text-muted-foreground">No scalar fields.</p>;
  }
  const colClass = columns === 3 ? "sm:grid-cols-3" : columns === 2 ? "sm:grid-cols-2" : "";
  return (
    <div className={cn("grid gap-x-6", colClass)}>
      {entries.map(([k, v]) => (
        <Metric key={k} label={titleCase(k)} value={renderScalar(v)} />
      ))}
    </div>
  );
}

/** Render an array of dicts as a table, columns inferred from the row keys. */
export function DictTable({
  rows,
  columns,
  maxColumns = 8,
}: {
  rows: Record<string, unknown>[];
  columns?: string[];
  maxColumns?: number;
}) {
  if (rows.length === 0) {
    return <p className="text-sm text-muted-foreground">No rows.</p>;
  }
  const keys =
    columns ??
    Array.from(
      rows.reduce<Set<string>>((set, row) => {
        for (const [k, v] of Object.entries(row)) if (isScalar(v)) set.add(k);
        return set;
      }, new Set<string>()),
    ).slice(0, maxColumns);

  return (
    <Table>
      <TableHeader>
        <TableRow className="text-xs text-muted-foreground">
          {keys.map((k) => (
            <TableHead key={k} className="whitespace-nowrap">
              {titleCase(k)}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row, i) => (
          <TableRow key={i}>
            {keys.map((k) => (
              <TableCell key={k} className="tnum whitespace-nowrap">
                {renderScalar(row[k])}
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
