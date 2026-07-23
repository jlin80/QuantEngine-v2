"use client";

import type { UseQueryResult } from "@tanstack/react-query";
import { Inbox, PlugZap, RefreshCw, TriangleAlert, WifiOff } from "lucide-react";

import type { ApiError } from "@/lib/api/client";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export function LoadingRows({ rows = 3, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn("space-y-2", className)}>
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-8 w-full" />
      ))}
    </div>
  );
}

function Panel({
  icon,
  title,
  detail,
  action,
  tone = "muted",
}: {
  icon: React.ReactNode;
  title: string;
  detail?: string;
  action?: React.ReactNode;
  tone?: "muted" | "warn" | "bear";
}) {
  const toneClass =
    tone === "bear" ? "text-bear" : tone === "warn" ? "text-warn" : "text-muted-foreground";
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border/70 px-4 py-8 text-center">
      <div className={cn("[&>svg]:size-6", toneClass)}>{icon}</div>
      <p className="text-sm font-medium">{title}</p>
      {detail ? <p className="max-w-sm text-xs text-muted-foreground">{detail}</p> : null}
      {action}
    </div>
  );
}

export function DisabledState({ label }: { label?: string }) {
  return (
    <Panel
      icon={<PlugZap />}
      title={label ?? "Subsystem disabled"}
      detail="This module is not enabled in the current engine profile."
    />
  );
}

export function UnreachableState({ onRetry }: { onRetry?: () => void }) {
  return (
    <Panel
      icon={<WifiOff />}
      tone="warn"
      title="Engine unreachable"
      detail="Could not reach the API. Is the engine running on :8000?"
      action={<RetryButton onRetry={onRetry} />}
    />
  );
}

export function ErrorState({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  if (error.status === 0) return <UnreachableState onRetry={onRetry} />;
  return (
    <Panel
      icon={<TriangleAlert />}
      tone="bear"
      title={`Error ${error.status || ""}`.trim()}
      detail={error.detail || error.message}
      action={<RetryButton onRetry={onRetry} />}
    />
  );
}

export function EmptyState({ label, detail }: { label?: string; detail?: string }) {
  return <Panel icon={<Inbox />} title={label ?? "Nothing here yet"} detail={detail} />;
}

function RetryButton({ onRetry }: { onRetry?: () => void }) {
  if (!onRetry) return null;
  return (
    <button
      type="button"
      onClick={onRetry}
      className="mt-1 inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
    >
      <RefreshCw className="size-3" />
      Retry
    </button>
  );
}

/**
 * Renders the right state (loading / disabled / error / empty / data) for a
 * query. Keeps every panel consistent and free of boilerplate.
 */
export function Async<T>({
  query,
  children,
  disabledLabel,
  isEmpty,
  emptyLabel,
  emptyDetail,
  skeleton,
}: {
  query: UseQueryResult<T, ApiError>;
  children: (data: T) => React.ReactNode;
  disabledLabel?: string;
  isEmpty?: (data: T) => boolean;
  emptyLabel?: string;
  emptyDetail?: string;
  skeleton?: React.ReactNode;
}) {
  if (query.isLoading) return <>{skeleton ?? <LoadingRows />}</>;
  if (query.error) {
    if (query.error.notEnabled) return <DisabledState label={disabledLabel} />;
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  }
  if (query.data === undefined) return <>{skeleton ?? <LoadingRows />}</>;
  if (isEmpty?.(query.data)) return <EmptyState label={emptyLabel} detail={emptyDetail} />;
  return <>{children(query.data)}</>;
}
