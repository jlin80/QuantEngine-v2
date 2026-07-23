"use client";

import { useState } from "react";
import { Download, FileText, Loader2, Play } from "lucide-react";

import { SectionCard } from "@/components/common/section-card";
import { Async, EmptyState } from "@/components/common/states";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useReports } from "@/lib/api/hooks";
import { useReportGenerate } from "@/lib/api/mutations";

const PERIODS = ["daily", "weekly", "monthly", "custom"];
const FORMATS = ["json", "markdown", "csv"];

const selectClass =
  "h-8 rounded-md border border-border bg-background px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50";

const MIME: Record<string, string> = {
  json: "application/json",
  markdown: "text/markdown",
  csv: "text/csv",
};

function download(filename: string, content: string, fmt: string) {
  const blob = new Blob([content], { type: MIME[fmt] ?? "text/plain" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function ReportGenerator() {
  const [period, setPeriod] = useState("daily");
  const [format, setFormat] = useState("json");
  const generate = useReportGenerate();

  const result = generate.data as
    | { content?: string; filename?: string; format?: string }
    | undefined;

  return (
    <SectionCard title="Generate report" icon={<FileText />}>
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Period
          <select className={selectClass} value={period} onChange={(e) => setPeriod(e.target.value)}>
            {PERIODS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Format
          <select className={selectClass} value={format} onChange={(e) => setFormat(e.target.value)}>
            {FORMATS.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
        </label>
        <Button
          size="sm"
          onClick={() => generate.mutate({ period, format })}
          disabled={generate.isPending}
        >
          {generate.isPending ? <Loader2 className="animate-spin" /> : <Play />}
          Generate
        </Button>
        {result?.content && (
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              download(result.filename ?? "report.txt", result.content ?? "", result.format ?? "json")
            }
          >
            <Download />
            Download
          </Button>
        )}
      </div>
      {result?.content && (
        <ScrollArea className="mt-4 h-80 rounded-md border border-border/60 bg-muted/20">
          <pre className="p-3 font-mono text-xs whitespace-pre-wrap">{result.content}</pre>
        </ScrollArea>
      )}
    </SectionCard>
  );
}

export function SavedReports() {
  const query = useReports();
  return (
    <SectionCard title="Saved reports" icon={<FileText />} contentClassName="pt-2">
      <Async
        query={query}
        disabledLabel="Reports unavailable"
        isEmpty={(d) => d.reports.length === 0}
        emptyLabel="No reports generated yet"
      >
        {(d) =>
          d.reports.length === 0 ? (
            <EmptyState label="No reports" />
          ) : (
            <ul className="space-y-1 text-sm">
              {d.reports.map((name) => (
                <li key={name} className="flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-muted/50">
                  <FileText className="size-3.5 text-muted-foreground" />
                  <span className="font-mono text-xs">{name}</span>
                </li>
              ))}
            </ul>
          )
        }
      </Async>
    </SectionCard>
  );
}
