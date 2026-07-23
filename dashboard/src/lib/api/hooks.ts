/**
 * TanStack Query hooks, one per REST endpoint.
 *
 * Polling intervals are a *fallback*: the WsProvider invalidates the relevant
 * keys on live events, so the UI feels instant while polling reconciles state
 * and covers periods with no events. Queries never retry on 4xx/503 (a disabled
 * subsystem is a stable state, not a transient failure).
 *
 * Each hook pins the error generic to ApiError so consumers can read
 * `query.error.notEnabled` without casting.
 */

import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { ApiError, apiGet } from "./client";
import type {
  AuditResponse,
  BacktestExperimentsResponse,
  CandlesResponse,
  ConfigResponse,
  Dict,
  DiscordStatus,
  LogsResponse,
  NotionStatus,
  ReportsResponse,
  ExecutionStatus,
  HealthSnapshot,
  MarketSnapshot,
  MarketTradesResponse,
  MlFeaturesResponse,
  MlModelsResponse,
  MlRankingResponse,
  OrderBook,
  Performance,
  PortfolioSnapshot,
  PositionsResponse,
  RiskStatus,
  StrategiesResponse,
  SymbolsResponse,
  SystemInfo,
  Ticker,
  TradesResponse,
} from "./types";

export const queryKeys = {
  health: ["health"] as const,
  systemStatus: ["system", "status"] as const,
  systemInfo: ["system", "info"] as const,
  executionStatus: ["execution", "status"] as const,
  portfolio: ["execution", "portfolio"] as const,
  positions: ["execution", "positions"] as const,
  trades: (limit: number) => ["execution", "trades", limit] as const,
  performance: ["execution", "performance"] as const,
  risk: ["execution", "risk"] as const,
  marketStatus: ["market", "status"] as const,
  symbols: ["market", "symbols"] as const,
  ticker: (symbol: string) => ["market", "ticker", symbol] as const,
  orderbook: (symbol: string, levels: number) =>
    ["market", "orderbook", symbol, levels] as const,
  candles: (symbol: string, tf: string, limit: number) =>
    ["market", "candles", symbol, tf, limit] as const,
  snapshot: (symbol: string) => ["market", "snapshot", symbol] as const,
  marketTrades: (symbol: string, limit: number) =>
    ["market", "trades", symbol, limit] as const,
  strategies: ["engine", "strategies"] as const,
  enginePerformance: ["engine", "performance"] as const,
  engineStatus: ["engine", "status"] as const,
  mlStatus: ["ml", "status"] as const,
  mlModels: ["ml", "models"] as const,
  mlRanking: ["ml", "ranking"] as const,
  mlFeatures: ["ml", "features"] as const,
  mlMeta: ["ml", "meta"] as const,
  backtestStatus: ["backtesting", "status"] as const,
  backtestCriteria: ["backtesting", "criteria"] as const,
  backtestExperiments: ["backtesting", "experiments"] as const,
  config: ["config"] as const,
  discord: ["integrations", "discord"] as const,
  notion: ["integrations", "notion"] as const,
  audit: (limit: number) => ["audit", limit] as const,
  logs: (limit: number, level: string | null) => ["logs", limit, level] as const,
  reports: ["reports"] as const,
  productionStatus: ["production", "status"] as const,
  securityReport: ["security", "report"] as const,
  backups: ["backups"] as const,
  updates: ["updates", "check"] as const,
};

function retry(failureCount: number, error: ApiError): boolean {
  // Don't hammer a disabled subsystem or a deterministic client error.
  if (error.notEnabled || (error.status >= 400 && error.status < 500)) return false;
  return failureCount < 2;
}

export function useHealth(): UseQueryResult<HealthSnapshot, ApiError> {
  return useQuery<HealthSnapshot, ApiError>({
    queryKey: queryKeys.health,
    queryFn: () => apiGet<HealthSnapshot>("/api/health"),
    refetchInterval: 5000,
    retry,
  });
}

export function useSystemStatus(): UseQueryResult<HealthSnapshot, ApiError> {
  return useQuery<HealthSnapshot, ApiError>({
    queryKey: queryKeys.systemStatus,
    queryFn: () => apiGet<HealthSnapshot>("/api/system/status"),
    refetchInterval: 5000,
    retry,
  });
}

export function useSystemInfo(): UseQueryResult<SystemInfo, ApiError> {
  return useQuery<SystemInfo, ApiError>({
    queryKey: queryKeys.systemInfo,
    queryFn: () => apiGet<SystemInfo>("/api/system/info"),
    staleTime: 60_000,
    retry,
  });
}

export function useExecutionStatus(): UseQueryResult<ExecutionStatus, ApiError> {
  return useQuery<ExecutionStatus, ApiError>({
    queryKey: queryKeys.executionStatus,
    queryFn: () => apiGet<ExecutionStatus>("/api/execution/status"),
    refetchInterval: 5000,
    retry,
  });
}

export function usePortfolio(): UseQueryResult<PortfolioSnapshot, ApiError> {
  return useQuery<PortfolioSnapshot, ApiError>({
    queryKey: queryKeys.portfolio,
    queryFn: () => apiGet<PortfolioSnapshot>("/api/execution/portfolio"),
    refetchInterval: 4000,
    retry,
  });
}

export function usePositions(): UseQueryResult<PositionsResponse, ApiError> {
  return useQuery<PositionsResponse, ApiError>({
    queryKey: queryKeys.positions,
    queryFn: () => apiGet<PositionsResponse>("/api/execution/positions"),
    refetchInterval: 4000,
    retry,
  });
}

export function useTrades(limit = 100): UseQueryResult<TradesResponse, ApiError> {
  return useQuery<TradesResponse, ApiError>({
    queryKey: queryKeys.trades(limit),
    queryFn: () => apiGet<TradesResponse>("/api/execution/trades", { limit }),
    refetchInterval: 8000,
    retry,
  });
}

export function usePerformance(): UseQueryResult<Performance, ApiError> {
  return useQuery<Performance, ApiError>({
    queryKey: queryKeys.performance,
    queryFn: () => apiGet<Performance>("/api/execution/performance"),
    refetchInterval: 8000,
    retry,
  });
}

export function useRisk(): UseQueryResult<RiskStatus, ApiError> {
  return useQuery<RiskStatus, ApiError>({
    queryKey: queryKeys.risk,
    queryFn: () => apiGet<RiskStatus>("/api/execution/risk"),
    refetchInterval: 6000,
    retry,
  });
}

export function useMarketStatus(): UseQueryResult<Record<string, unknown>, ApiError> {
  return useQuery<Record<string, unknown>, ApiError>({
    queryKey: queryKeys.marketStatus,
    queryFn: () => apiGet<Record<string, unknown>>("/api/market/status"),
    refetchInterval: 5000,
    retry,
  });
}

export function useSymbols(): UseQueryResult<SymbolsResponse, ApiError> {
  return useQuery<SymbolsResponse, ApiError>({
    queryKey: queryKeys.symbols,
    queryFn: () => apiGet<SymbolsResponse>("/api/market/symbols"),
    refetchInterval: 5000,
    retry,
  });
}

export function useTicker(symbol: string | null): UseQueryResult<Ticker, ApiError> {
  return useQuery<Ticker, ApiError>({
    queryKey: queryKeys.ticker(symbol ?? ""),
    queryFn: () => apiGet<Ticker>(`/api/market/ticker/${symbol}`),
    enabled: Boolean(symbol),
    refetchInterval: 2000,
    retry,
  });
}

export function useOrderBook(
  symbol: string | null,
  levels = 12,
): UseQueryResult<OrderBook, ApiError> {
  return useQuery<OrderBook, ApiError>({
    queryKey: queryKeys.orderbook(symbol ?? "", levels),
    queryFn: () => apiGet<OrderBook>(`/api/market/orderbook/${symbol}`, { levels }),
    enabled: Boolean(symbol),
    refetchInterval: 2000,
    retry,
  });
}

export function useCandles(
  symbol: string | null,
  tf = "1m",
  limit = 200,
): UseQueryResult<CandlesResponse, ApiError> {
  return useQuery<CandlesResponse, ApiError>({
    queryKey: queryKeys.candles(symbol ?? "", tf, limit),
    queryFn: () => apiGet<CandlesResponse>(`/api/market/candles/${symbol}`, { tf, limit }),
    enabled: Boolean(symbol),
    refetchInterval: 10_000,
    retry,
  });
}

export function useMarketSnapshot(
  symbol: string | null,
): UseQueryResult<MarketSnapshot, ApiError> {
  return useQuery<MarketSnapshot, ApiError>({
    queryKey: queryKeys.snapshot(symbol ?? ""),
    queryFn: () => apiGet<MarketSnapshot>(`/api/market/snapshot/${symbol}`),
    enabled: Boolean(symbol),
    refetchInterval: 4000,
    retry,
  });
}

export function useMarketTrades(
  symbol: string | null,
  limit = 50,
): UseQueryResult<MarketTradesResponse, ApiError> {
  return useQuery<MarketTradesResponse, ApiError>({
    queryKey: queryKeys.marketTrades(symbol ?? "", limit),
    queryFn: () => apiGet<MarketTradesResponse>(`/api/market/trades/${symbol}`, { limit }),
    enabled: Boolean(symbol),
    refetchInterval: 2000,
    retry,
  });
}

// ------------------------------------------------------------------ engine

export function useStrategies(): UseQueryResult<StrategiesResponse, ApiError> {
  return useQuery<StrategiesResponse, ApiError>({
    queryKey: queryKeys.strategies,
    queryFn: () => apiGet<StrategiesResponse>("/api/engine/strategies"),
    refetchInterval: 6000,
    retry,
  });
}

export function useEnginePerformance(): UseQueryResult<Dict, ApiError> {
  return useQuery<Dict, ApiError>({
    queryKey: queryKeys.enginePerformance,
    queryFn: () => apiGet<Dict>("/api/engine/performance"),
    refetchInterval: 8000,
    retry,
  });
}

export function useEngineStatus(): UseQueryResult<Dict, ApiError> {
  return useQuery<Dict, ApiError>({
    queryKey: queryKeys.engineStatus,
    queryFn: () => apiGet<Dict>("/api/engine/status"),
    refetchInterval: 6000,
    retry,
  });
}

// ------------------------------------------------------------------ machine learning

export function useMlStatus(): UseQueryResult<Dict, ApiError> {
  return useQuery<Dict, ApiError>({
    queryKey: queryKeys.mlStatus,
    queryFn: () => apiGet<Dict>("/api/ml/status"),
    refetchInterval: 8000,
    retry,
  });
}

export function useMlModels(): UseQueryResult<MlModelsResponse, ApiError> {
  return useQuery<MlModelsResponse, ApiError>({
    queryKey: queryKeys.mlModels,
    queryFn: () => apiGet<MlModelsResponse>("/api/ml/models"),
    refetchInterval: 10_000,
    retry,
  });
}

export function useMlRanking(): UseQueryResult<MlRankingResponse, ApiError> {
  return useQuery<MlRankingResponse, ApiError>({
    queryKey: queryKeys.mlRanking,
    queryFn: () => apiGet<MlRankingResponse>("/api/ml/ranking"),
    refetchInterval: 10_000,
    retry,
  });
}

export function useMlFeatures(): UseQueryResult<MlFeaturesResponse, ApiError> {
  return useQuery<MlFeaturesResponse, ApiError>({
    queryKey: queryKeys.mlFeatures,
    queryFn: () => apiGet<MlFeaturesResponse>("/api/ml/features"),
    refetchInterval: 30_000,
    retry,
  });
}

export function useMlMeta(): UseQueryResult<Dict, ApiError> {
  return useQuery<Dict, ApiError>({
    queryKey: queryKeys.mlMeta,
    queryFn: () => apiGet<Dict>("/api/ml/meta"),
    refetchInterval: 10_000,
    retry,
  });
}

// ------------------------------------------------------------------ backtesting

export function useBacktestStatus(): UseQueryResult<Dict, ApiError> {
  return useQuery<Dict, ApiError>({
    queryKey: queryKeys.backtestStatus,
    queryFn: () => apiGet<Dict>("/api/backtesting/status"),
    refetchInterval: 8000,
    retry,
  });
}

export function useBacktestCriteria(): UseQueryResult<Dict, ApiError> {
  return useQuery<Dict, ApiError>({
    queryKey: queryKeys.backtestCriteria,
    queryFn: () => apiGet<Dict>("/api/backtesting/criteria"),
    staleTime: 60_000,
    retry,
  });
}

export function useBacktestExperiments(
  limit = 50,
): UseQueryResult<BacktestExperimentsResponse, ApiError> {
  return useQuery<BacktestExperimentsResponse, ApiError>({
    queryKey: queryKeys.backtestExperiments,
    queryFn: () =>
      apiGet<BacktestExperimentsResponse>("/api/backtesting/experiments", { limit }),
    refetchInterval: 8000,
    retry,
  });
}

// ------------------------------------------------------------------ command layer

export function useConfig(): UseQueryResult<ConfigResponse, ApiError> {
  return useQuery<ConfigResponse, ApiError>({
    queryKey: queryKeys.config,
    queryFn: () => apiGet<ConfigResponse>("/api/config"),
    refetchInterval: 15_000,
    retry,
  });
}

export function useDiscordStatus(): UseQueryResult<DiscordStatus, ApiError> {
  return useQuery<DiscordStatus, ApiError>({
    queryKey: queryKeys.discord,
    queryFn: () => apiGet<DiscordStatus>("/api/integrations/discord"),
    refetchInterval: 15_000,
    retry,
  });
}

export function useNotionStatus(): UseQueryResult<NotionStatus, ApiError> {
  return useQuery<NotionStatus, ApiError>({
    queryKey: queryKeys.notion,
    queryFn: () => apiGet<NotionStatus>("/api/integrations/notion"),
    refetchInterval: 30_000,
    retry,
  });
}

export function useAudit(limit = 100): UseQueryResult<AuditResponse, ApiError> {
  return useQuery<AuditResponse, ApiError>({
    queryKey: queryKeys.audit(limit),
    queryFn: () => apiGet<AuditResponse>("/api/audit", { limit }),
    refetchInterval: 8000,
    retry,
  });
}

export function useLogs(
  limit = 200,
  level: string | null = null,
): UseQueryResult<LogsResponse, ApiError> {
  return useQuery<LogsResponse, ApiError>({
    queryKey: queryKeys.logs(limit, level),
    queryFn: () => apiGet<LogsResponse>("/api/logs", { limit, level: level ?? undefined }),
    refetchInterval: 5000,
    retry,
  });
}

export function useReports(): UseQueryResult<ReportsResponse, ApiError> {
  return useQuery<ReportsResponse, ApiError>({
    queryKey: queryKeys.reports,
    queryFn: () => apiGet<ReportsResponse>("/api/reports"),
    refetchInterval: 15_000,
    retry,
  });
}

// ------------------------------------------------------------------ production (Fase 9)

export function useProductionStatus(): UseQueryResult<Dict, ApiError> {
  return useQuery<Dict, ApiError>({
    queryKey: queryKeys.productionStatus,
    queryFn: () => apiGet<Dict>("/api/production/status"),
    refetchInterval: 6000,
    retry,
  });
}

export function useSecurityReport(): UseQueryResult<Dict, ApiError> {
  return useQuery<Dict, ApiError>({
    queryKey: queryKeys.securityReport,
    queryFn: () => apiGet<Dict>("/api/security/report"),
    refetchInterval: 30_000,
    retry,
  });
}

export function useBackups(): UseQueryResult<Dict, ApiError> {
  return useQuery<Dict, ApiError>({
    queryKey: queryKeys.backups,
    queryFn: () => apiGet<Dict>("/api/backups"),
    refetchInterval: 15_000,
    retry,
  });
}

export function useUpdates(): UseQueryResult<Dict, ApiError> {
  return useQuery<Dict, ApiError>({
    queryKey: queryKeys.updates,
    queryFn: () => apiGet<Dict>("/api/updates/check"),
    refetchInterval: 60_000,
    retry,
  });
}
