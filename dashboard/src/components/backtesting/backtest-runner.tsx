"use client";

import { useState } from "react";
import { Loader2, Play, X } from "lucide-react";

import { MetricsGrid } from "@/components/common/metrics-grid";
import { SectionCard } from "@/components/common/section-card";
import { Button } from "@/components/ui/button";
import { useStrategies, useSymbols } from "@/lib/api/hooks";
import { useBacktestCancel, useBacktestRun } from "@/lib/api/mutations";

const inputClass =
  "h-8 rounded-md border border-border bg-background px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-xs text-muted-foreground">
      {label}
      {children}
    </label>
  );
}

export function BacktestRunner() {
  const symbols = useSymbols();
  const strategies = useStrategies();
  const run = useBacktestRun();
  const cancel = useBacktestCancel();

  const [symbol, setSymbol] = useState("");
  const [strategy, setStrategy] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");

  const symbolOptions = symbols.data ? Object.keys(symbols.data.symbols) : [];
  const strategyOptions = strategies.data?.strategies.map((s) => s.name) ?? [];
  const result = run.data as Record<string, unknown> | undefined;
  const jobId = typeof result?.job_id === "string" ? result.job_id : null;

  return (
    <SectionCard title="Run backtest" icon={<Play />}>
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Symbol">
          <select className={inputClass} value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            <option value="">Select…</option>
            {symbolOptions.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Strategy">
          <select
            className={inputClass}
            value={strategy}
            onChange={(e) => setStrategy(e.target.value)}
          >
            <option value="">Select…</option>
            {strategyOptions.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Start">
          <input type="date" className={inputClass} value={start} onChange={(e) => setStart(e.target.value)} />
        </Field>
        <Field label="End">
          <input type="date" className={inputClass} value={end} onChange={(e) => setEnd(e.target.value)} />
        </Field>
        <Button
          size="sm"
          onClick={() => run.mutate({ symbol, strategy, start, end })}
          disabled={run.isPending || !symbol || !strategy}
        >
          {run.isPending ? <Loader2 className="animate-spin" /> : <Play />}
          Run
        </Button>
        {jobId && (
          <Button
            size="sm"
            variant="destructive"
            onClick={() => cancel.mutate(jobId)}
            disabled={cancel.isPending}
          >
            <X />
            Cancel
          </Button>
        )}
      </div>
      {result && (
        <div className="mt-4 border-t pt-3">
          <MetricsGrid data={result} columns={3} />
        </div>
      )}
      <p className="mt-3 text-[11px] text-muted-foreground">
        Backtests run server-side via the BacktestLab and are recorded as experiments below.
      </p>
    </SectionCard>
  );
}
