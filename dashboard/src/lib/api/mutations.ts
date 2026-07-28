"use client";

import { useMutation, useQueryClient, type UseMutationResult } from "@tanstack/react-query";
import { toast } from "sonner";

import { ApiError, apiSend } from "./client";
import type { Dict } from "./types";

/**
 * Shared mutation helper: toasts on success/failure and invalidates the given
 * query-key prefixes so the affected panels refetch. All writes go through the
 * backend command layer, which audits them and forbids enabling live trading.
 */
function useAction<TInput, TResult>(opts: {
  run: (input: TInput) => Promise<TResult>;
  success: string;
  invalidate?: string[];
}): UseMutationResult<TResult, ApiError, TInput> {
  const queryClient = useQueryClient();
  return useMutation<TResult, ApiError, TInput>({
    mutationFn: opts.run,
    onSuccess: () => {
      toast.success(opts.success);
      for (const key of opts.invalidate ?? []) {
        queryClient.invalidateQueries({ queryKey: [key] });
      }
    },
    onError: (error) => toast.error("Action failed", { description: error.detail || error.message }),
  });
}

// ------------------------------------------------------------------ ML actions

export function useMlTrain(): UseMutationResult<Dict, ApiError, void> {
  return useAction<void, Dict>({
    run: () => apiSend<Dict>("POST", "/api/ml/train"),
    success: "Training run started",
    invalidate: ["ml"],
  });
}

export function useMlDriftCheck(): UseMutationResult<Dict, ApiError, void> {
  return useAction<void, Dict>({
    run: () => apiSend<Dict>("POST", "/api/ml/drift/check"),
    success: "Drift check completed",
    invalidate: ["ml"],
  });
}

export function useMlMetaEvaluate(): UseMutationResult<Dict, ApiError, void> {
  return useAction<void, Dict>({
    run: () => apiSend<Dict>("POST", "/api/ml/meta/evaluate"),
    success: "Meta evaluation completed",
    invalidate: ["ml"],
  });
}

// ------------------------------------------------------------------ strategy control

export function useStrategyEnable(): UseMutationResult<Dict, ApiError, string> {
  return useAction<string, Dict>({
    run: (name) => apiSend<Dict>("POST", `/api/engine/strategies/${name}/enable`),
    success: "Strategy enabled",
    invalidate: ["engine"],
  });
}

export function useStrategyDisable(): UseMutationResult<Dict, ApiError, string> {
  return useAction<string, Dict>({
    run: (name) => apiSend<Dict>("POST", `/api/engine/strategies/${name}/disable`),
    success: "Strategy disabled",
    invalidate: ["engine"],
  });
}

export function useStrategyWeight(): UseMutationResult<
  Dict,
  ApiError,
  { name: string; weight: number }
> {
  return useAction<{ name: string; weight: number }, Dict>({
    run: ({ name, weight }) =>
      apiSend<Dict>("PATCH", `/api/engine/strategies/${name}/weight`, { weight }),
    success: "Weight updated",
    invalidate: ["engine"],
  });
}

// ------------------------------------------------------------------ config

/**
 * Saves a config patch. The backend distinguishes settings applied live from
 * those the engine only reads at startup; surfacing that is essential — without
 * it a toggle like `trailing_enabled` just said "saved" while the running engine
 * kept the old value, which made the whole panel look decorative.
 */
export function useConfigPatch(): UseMutationResult<Dict, ApiError, Record<string, unknown>> {
  const queryClient = useQueryClient();
  return useMutation<Dict, ApiError, Record<string, unknown>>({
    mutationFn: (patch) => apiSend<Dict>("PATCH", "/api/config", patch),
    onSuccess: (data) => {
      const restart = (data?.needs_restart as string[] | undefined) ?? [];
      const live = (data?.applied_live as string[] | undefined) ?? [];
      if (restart.length > 0) {
        toast.warning("Saved — restart required", {
          description:
            `${restart.join(", ")} ` +
            `${restart.length === 1 ? "is" : "are"} only read at startup. ` +
            `Restart the engine to apply.` +
            (live.length > 0 ? ` Applied live: ${live.join(", ")}.` : ""),
          duration: 10_000,
        });
      } else {
        toast.success("Configuration applied live", {
          description: live.length > 0 ? live.join(", ") : undefined,
        });
      }
      for (const key of ["config", "execution", "engine"]) {
        queryClient.invalidateQueries({ queryKey: [key] });
      }
    },
    onError: (error) => toast.error("Action failed", { description: error.detail || error.message }),
  });
}

// ------------------------------------------------------------------ integrations

export function useDiscordTest(): UseMutationResult<Dict, ApiError, void> {
  return useAction<void, Dict>({
    run: () => apiSend<Dict>("POST", "/api/integrations/discord/test"),
    success: "Test notification sent",
    invalidate: ["integrations"],
  });
}

// ------------------------------------------------------------------ backtesting

export function useBacktestRun(): UseMutationResult<Dict, ApiError, Record<string, unknown>> {
  return useAction<Record<string, unknown>, Dict>({
    run: (payload) => apiSend<Dict>("POST", "/api/backtesting/run", payload),
    success: "Backtest queued",
    invalidate: ["backtesting"],
  });
}

export function useBacktestCancel(): UseMutationResult<Dict, ApiError, string> {
  return useAction<string, Dict>({
    run: (jobId) => apiSend<Dict>("POST", `/api/backtesting/cancel/${jobId}`),
    success: "Backtest cancelled",
    invalidate: ["backtesting"],
  });
}

// ------------------------------------------------------------------ reports

export function useReportGenerate(): UseMutationResult<
  Dict,
  ApiError,
  { period: string; format: string }
> {
  return useAction<{ period: string; format: string }, Dict>({
    run: (payload) => apiSend<Dict>("POST", "/api/reports/generate", payload),
    success: "Report generated",
    invalidate: ["reports"],
  });
}

// ------------------------------------------------------------------ production (Fase 9)

export function useBackupCreate(): UseMutationResult<Dict, ApiError, void> {
  return useAction<void, Dict>({
    run: () => apiSend<Dict>("POST", "/api/backups/create", {}),
    success: "Backup created",
    invalidate: ["backups", "production"],
  });
}

export function useBackupRestore(): UseMutationResult<Dict, ApiError, string> {
  return useAction<string, Dict>({
    run: (backupId) => apiSend<Dict>("POST", "/api/backups/restore", { backup_id: backupId }),
    success: "Backup restored",
    invalidate: ["backups", "production"],
  });
}

export function useMaintenanceEnter(): UseMutationResult<Dict, ApiError, string> {
  return useAction<string, Dict>({
    run: (reason) => apiSend<Dict>("POST", "/api/maintenance/enter", { reason }),
    success: "Maintenance window opened",
    invalidate: ["production"],
  });
}

export function useMaintenanceExit(): UseMutationResult<Dict, ApiError, void> {
  return useAction<void, Dict>({
    run: () => apiSend<Dict>("POST", "/api/maintenance/exit", {}),
    success: "Maintenance window closed",
    invalidate: ["production"],
  });
}

export function useImprovementAnalyze(): UseMutationResult<Dict, ApiError, void> {
  return useAction<void, Dict>({
    run: () => apiSend<Dict>("POST", "/api/improvement/analyze"),
    success: "Improvement analysis complete",
    invalidate: [],
  });
}

export function useNotionSync(): UseMutationResult<Dict, ApiError, void> {
  return useAction<void, Dict>({
    run: () => apiSend<Dict>("POST", "/api/notion/sync"),
    success: "Notion queue synced",
    invalidate: ["integrations", "production"],
  });
}

export function useOperationalReport(): UseMutationResult<Dict, ApiError, string> {
  return useAction<string, Dict>({
    run: (period) => apiSend<Dict>("POST", "/api/reports/send", { period }),
    success: "Operational report sent",
    invalidate: [],
  });
}
