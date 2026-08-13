/**
 * TypeScript mirrors of the engine's `.to_dict()` payloads.
 *
 * The FastAPI routes return `dict[str, Any]`, so OpenAPI carries no field-level
 * schema — these interfaces are hand-maintained against the domain models in
 * `app/execution/models`, `app/market/models`, `app/monitoring/health.py`.
 * Unknown/loosely-typed nested blobs are typed as `Record<string, unknown>`.
 */

// ---------------------------------------------------------------- health/system

export type HealthStatus = "healthy" | "degraded" | "unhealthy" | string;

export interface RecentError {
  timestamp: string;
  level: string;
  logger: string;
  message: string;
}

export interface HealthSnapshot {
  status: HealthStatus;
  timestamp: string;
  uptime_seconds: number;
  cpu_percent: number;
  memory_percent: number;
  memory_used_mb: number;
  disk_percent: number;
  event_loop_lag_ms: number;
  event_bus: Record<string, unknown>;
  components: Record<string, string>;
  recent_errors: RecentError[];
}

export interface SystemInfo {
  app: string;
  environment: string;
  instruments: string[];
  phase: string;
}

// ---------------------------------------------------------------- execution

export type OrderSide = "buy" | "sell" | string;
export type PositionStatus = "open" | "closed" | "closing" | string;

export interface PortfolioSnapshot {
  timestamp: string;
  initial_balance: number;
  balance: number;
  equity: number;
  floating_pnl: number;
  realized_pnl: number;
  used_capital: number;
  free_capital: number;
  exposure: number;
  exposure_pct: number;
  peak_equity: number;
  drawdown_pct: number;
  open_positions: number;
  total_trades: number;
  return_pct: number;
  base_currency: string;
}

export interface Position {
  position_id: string;
  symbol: string;
  side: OrderSide;
  quantity: number;
  initial_quantity: number;
  entry_price: number;
  initial_stop: number | null;
  stop_loss: number | null;
  take_profit: number | null;
  opened_at: string | null;
  closed_at: string | null;
  status: PositionStatus;
  mark_price: number | null;
  unrealized_pnl: number;
  realized_pnl: number;
  r_multiple: number | null;
  notional: number;
  commission_paid: number;
  break_even_active: boolean;
  trailing_active: boolean;
  exit_reason: string | null;
  decision_id: string | null;
  regime: string | null;
  score: number | null;
  confidence?: number | null;
  entry_reason?: string | null;
}

export interface PositionsResponse {
  open: Position[];
  closed: Position[];
}

export interface Order {
  request_id: string;
  symbol: string;
  side: OrderSide;
  quantity: number;
  order_type: string;
  time_in_force: string;
  limit_price: number | null;
  stop_price: number | null;
  stop_loss: number | null;
  take_profit: number | null;
  reduce_only: boolean;
  decision_id: string | null;
  reason: string | null;
  created_at: string | null;
  metadata: Record<string, unknown>;
}

export interface Trade {
  trade_id: string;
  position_id: string;
  symbol: string;
  side: OrderSide;
  quantity: number;
  entry_time: string | null;
  exit_time: string | null;
  duration_seconds: number | null;
  entry_price: number;
  exit_price: number;
  stop_loss: number | null;
  take_profit: number | null;
  commission: number;
  slippage_bps: number;
  spread_bps: number;
  pnl: number;
  pnl_gross: number;
  r_multiple: number | null;
  return_pct: number;
  is_win: boolean;
  atr: number | null;
  volatility: number | null;
  regime: string | null;
  score: number | null;
  confidence?: number | null;
}

export interface TradesResponse {
  trades: Trade[];
}

export interface Performance {
  total_trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  loss_rate: number;
  net_profit: number;
  gross_profit: number;
  gross_loss: number;
  profit_factor: number;
  expectancy: number;
  expectancy_r: number;
  average_win: number;
  average_loss: number;
  risk_reward: number;
  largest_win: number;
  largest_loss: number;
  max_drawdown: number;
  max_drawdown_pct: number;
  sharpe: number;
  sortino: number;
  calmar: number;
  ulcer_index: number;
  recovery_factor: number;
  average_holding_seconds: number;
  trades_per_day: number;
  trades_by_session: Record<string, number>;
}

export interface RiskLimits {
  max_risk_per_trade_pct: number;
  max_daily_loss_pct: number;
  max_open_positions: number;
  max_exposure_pct: number;
  kill_switch_drawdown_pct: number;
}

export interface RiskStatus {
  kill_switch: boolean;
  kill_reason: string;
  circuit_breaker: boolean;
  circuit_reason: string;
  consecutive_losses: number;
  drawdown_pct: number;
  /** Operator override: no drawdown-based halt is enforced while true. */
  ignore_drawdown_limits: boolean;
  realized_today: number;
  limits: RiskLimits;
}

export interface ExecutionStatus {
  mode: string;
  enabled: boolean;
  portfolio: Record<string, unknown>;
  positions: Record<string, unknown>;
  orders: Record<string, unknown>;
  risk: RiskStatus;
  paper: Record<string, unknown>;
  journal: Record<string, unknown>;
}

// ---------------------------------------------------------------- market

export interface Ticker {
  symbol: string;
  provider: string;
  bid: number | null;
  ask: number | null;
  bid_size: number | null;
  ask_size: number | null;
  last: number | null;
  mid: number | null;
  spread: number | null;
  spread_bps: number | null;
  mark_price: number | null;
  index_price: number | null;
  exchange_ts: string | null;
  local_ts: string | null;
  latency_ms: number | null;
}

export interface Candle {
  symbol: string;
  provider: string;
  timeframe: string;
  start: string;
  end: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  buy_volume: number;
  sell_volume: number;
  vwap: number | null;
  trades: number;
  closed: boolean;
  source: string;
}

export interface CandlesResponse {
  symbol: string;
  timeframe: string;
  candles: Candle[];
}

export interface DepthLevel {
  price: number;
  size: number;
}

export interface OrderBook {
  symbol: string;
  provider: string;
  bids: DepthLevel[];
  asks: DepthLevel[];
  sequence: number;
  best_bid: number | null;
  best_ask: number | null;
  spread: number | null;
  mid: number | null;
  microprice: number | null;
  imbalance: number | null;
  book_pressure: number | null;
  exchange_ts: string | null;
  local_ts: string | null;
}

export interface MarketState {
  symbol: string;
  provider: string;
  subscribed: boolean;
  connected: boolean;
  ticks_received: number;
  last_update: string | null;
  staleness_seconds: number | null;
}

export interface SymbolsResponse {
  symbols: Record<string, MarketState>;
}

/** Composite snapshot — loosely typed (fields depend on feed availability). */
export interface MarketSnapshot {
  symbol: string;
  provider: string;
  generated_at: string;
  ticker?: Ticker | null;
  orderbook?: OrderBook | null;
  candle?: Candle | null;
  regime?: string | null;
  volatility?: number | null;
  atr?: number | null;
  vwap?: number | null;
  delta?: number | null;
  cvd?: number | null;
  book_pressure?: number | null;
  imbalance?: number | null;
  absorption?: number | null;
  [key: string]: unknown;
}

export interface MarketTrade {
  symbol: string;
  provider: string;
  trade_id: string | null;
  price: number;
  size: number;
  side: string;
  exchange_ts: string | null;
  local_ts: string | null;
  latency_ms: number | null;
}

export interface MarketTradesResponse {
  symbol: string;
  trades: MarketTrade[];
}

// ---------------------------------------------------------------- engine / strategies

export interface StrategyStats {
  name: string;
  version: string;
  enabled: boolean;
  symbols: string[];
  cadence: string;
  weight: number;
  runs: number;
  errors: number;
  skipped: number;
  signals_produced: number;
  last_run_at: string | null;
  last_duration_ms: number | null;
  avg_duration_ms: number | null;
  last_signal_at: string | null;
  last_score: number | null;
  last_confidence: number | null;
}

export interface StrategiesResponse {
  strategies: StrategyStats[];
  explanations: Record<string, string>;
}

// ML / backtesting payloads are shape-rich and evolving; typed as loose records
// and rendered via introspection (MetricsGrid / tables).
export type Dict = Record<string, unknown>;

export interface MlModelsResponse {
  models: Dict[];
  active: Dict | null;
  history: unknown[];
}

export interface MlRankingResponse {
  ranking: Dict[];
}

export interface MlFeaturesResponse {
  features: Dict[];
}

export interface BacktestExperimentsResponse {
  count: number;
  experiments: Dict[];
}

// ---------------------------------------------------------------- command layer

export type ConfigKind = "bool" | "number" | "dict" | "list" | "string";

export interface ConfigEntry {
  value: unknown;
  overridden: boolean;
  default: unknown;
  /** Tipo declarado en el esquema. No se deduce del valor: un dict vacío no dice nada. */
  kind: ConfigKind;
  /** El motor lo lee en cada evaluación; si es false, el cambio exige reinicio. */
  live: boolean;
}

export interface ConfigResponse {
  mode: string;
  live_enabled: boolean;
  config: Record<string, ConfigEntry>;
}

export interface DiscordStatus {
  enabled: boolean;
  configured: boolean;
  min_level: string;
  webhook_masked: string;
  stats: Record<string, number>;
}

export interface NotionStatus {
  enabled: boolean;
  configured: boolean;
  has_database: boolean;
}

export interface AuditEntry {
  timestamp: string;
  action: string;
  actor: string;
  target: string | null;
  before: unknown;
  after: unknown;
  meta: Record<string, unknown>;
}

export interface AuditResponse {
  entries: AuditEntry[];
}

export interface LogRecord {
  timestamp: string;
  level: string;
  logger: string;
  message: string;
}

export interface LogsResponse {
  logs: LogRecord[];
  count: number;
}

export interface ReportsResponse {
  reports: string[];
}
