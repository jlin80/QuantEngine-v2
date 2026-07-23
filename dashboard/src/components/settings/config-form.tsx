"use client";

import { useState } from "react";
import { Loader2, RotateCcw, Save, SlidersHorizontal } from "lucide-react";

import { SectionCard } from "@/components/common/section-card";
import { Async } from "@/components/common/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useConfig } from "@/lib/api/hooks";
import { useConfigPatch } from "@/lib/api/mutations";
import type { ConfigEntry, ConfigResponse } from "@/lib/api/types";
import { titleCase } from "@/lib/format";

const inputClass =
  "h-7 w-40 rounded-md border border-border bg-background px-2 text-sm tnum outline-none focus-visible:ring-2 focus-visible:ring-ring/50";

const LEVELS = ["info", "success", "warning", "error", "critical"];

function section(path: string): string {
  return path.split(".")[0];
}

function Field({
  path,
  entry,
  value,
  onChange,
}: {
  path: string;
  entry: ConfigEntry;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const label = titleCase(path.split(".").slice(1).join(" ") || path);
  const base = entry.default;

  let control: React.ReactNode;
  if (typeof base === "boolean") {
    control = (
      <button
        type="button"
        onClick={() => onChange(!(value as boolean))}
        className={`h-6 w-11 rounded-full border transition-colors ${
          value ? "border-bull/50 bg-bull/30" : "border-border bg-muted"
        }`}
      >
        <span
          className={`block size-4 rounded-full bg-foreground/80 transition-transform ${
            value ? "translate-x-6" : "translate-x-1"
          }`}
        />
      </button>
    );
  } else if (path.endsWith("min_level")) {
    control = (
      <select
        className={inputClass}
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
      >
        {LEVELS.map((l) => (
          <option key={l} value={l}>
            {l}
          </option>
        ))}
      </select>
    );
  } else if (typeof base === "number") {
    control = (
      <input
        type="number"
        step="any"
        className={inputClass}
        value={value === null || value === undefined ? "" : String(value)}
        onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
      />
    );
  } else {
    control = (
      <input
        className={inputClass}
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }

  return (
    <div className="flex items-center justify-between gap-3 py-1.5">
      <span className="flex items-center gap-2 text-sm">
        {label}
        {entry.overridden && (
          <Badge variant="outline" className="border-info/40 text-info">
            override
          </Badge>
        )}
      </span>
      {control}
    </div>
  );
}

function Editor({ data }: { data: ConfigResponse }) {
  const [drafts, setDrafts] = useState<Record<string, unknown>>({});
  const patch = useConfigPatch();

  const entries = Object.entries(data.config);
  const groups = entries.reduce<Record<string, [string, ConfigEntry][]>>((acc, [path, entry]) => {
    (acc[section(path)] ??= []).push([path, entry]);
    return acc;
  }, {});

  const dirty = Object.keys(drafts).length > 0;
  const valueOf = (path: string, entry: ConfigEntry) =>
    path in drafts ? drafts[path] : entry.value;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          Mode <span className="text-foreground uppercase">{data.mode}</span> · live disabled
        </p>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setDrafts({})}
            disabled={!dirty || patch.isPending}
          >
            <RotateCcw />
            Reset
          </Button>
          <Button
            size="sm"
            onClick={() => patch.mutate(drafts, { onSuccess: () => setDrafts({}) })}
            disabled={!dirty || patch.isPending}
          >
            {patch.isPending ? <Loader2 className="animate-spin" /> : <Save />}
            Save {dirty ? `(${Object.keys(drafts).length})` : ""}
          </Button>
        </div>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        {Object.entries(groups).map(([group, rows]) => (
          <div key={group} className="rounded-lg border border-border/60 p-3">
            <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
              {group}
            </p>
            {rows.map(([path, entry]) => (
              <Field
                key={path}
                path={path}
                entry={entry}
                value={valueOf(path, entry)}
                onChange={(v) => setDrafts((d) => ({ ...d, [path]: v }))}
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

export function ConfigForm() {
  const query = useConfig();
  return (
    <SectionCard title="Configuration" icon={<SlidersHorizontal />}>
      <Async query={query} disabledLabel="Config API unavailable">
        {(data) => <Editor data={data} />}
      </Async>
    </SectionCard>
  );
}
