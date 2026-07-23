/**
 * Contract for the `/ws/events` stream.
 *
 * The backend broadcasts every bus event as `event.to_dict()`:
 *   { event_id, occurred_at (ISO), source, ...payload, event: "<ClassName>" }
 * See app/core/events/base.py and app/dashboard/api/websocket.py.
 */

export interface BusEvent {
  event: string;
  event_id: string;
  occurred_at: string;
  source: string;
  [key: string]: unknown;
}

/** Event class names emitted by the engine (subset used by the dashboard). */
export const EventName = {
  // market
  PriceUpdated: "PriceUpdated",
  // execution — positions & orders
  OrderCreated: "OrderCreated",
  OrderExecuted: "OrderExecuted",
  OrderFilled: "OrderFilled",
  OrderRejected: "OrderRejected",
  PositionOpened: "PositionOpened",
  PositionModified: "PositionModified",
  PositionClosed: "PositionClosed",
  StopMoved: "StopMoved",
  TrailingUpdated: "TrailingUpdated",
  BreakEvenActivated: "BreakEvenActivated",
  PaperTradeExecuted: "PaperTradeExecuted",
  TradeOpened: "TradeOpened",
  TradeClosed: "TradeClosed",
  // risk
  RiskTriggered: "RiskTriggered",
  KillSwitchTriggered: "KillSwitchTriggered",
  CircuitBreakerTriggered: "CircuitBreakerTriggered",
  // system / connections
  SystemStarted: "SystemStarted",
  SystemStopping: "SystemStopping",
  ModuleHealthChanged: "ModuleHealthChanged",
  ModuleFrozen: "ModuleFrozen",
  ConnectionLost: "ConnectionLost",
  ConnectionRestored: "ConnectionRestored",
} as const;

export type EventNameKey = keyof typeof EventName;

export type AlertSeverity = "critical" | "warning" | "info";

/** Events that should surface in the alerts feed, with a severity. */
export const ALERT_SEVERITY: Record<string, AlertSeverity> = {
  KillSwitchTriggered: "critical",
  CircuitBreakerTriggered: "critical",
  ModuleFrozen: "critical",
  ConnectionLost: "critical",
  RiskTriggered: "warning",
  OrderRejected: "warning",
  ModuleHealthChanged: "warning",
  SystemStopping: "warning",
  ConnectionRestored: "info",
  SystemStarted: "info",
};

export function alertSeverityFor(event: string): AlertSeverity | null {
  return ALERT_SEVERITY[event] ?? null;
}

/** Best-effort extraction of a symbol field from any event payload. */
export function eventSymbol(e: BusEvent): string | null {
  const s = e.symbol;
  return typeof s === "string" ? s : null;
}

/** Best-effort extraction of a price field from a PriceUpdated payload. */
export function eventPrice(e: BusEvent): number | null {
  for (const key of ["price", "last", "mark_price", "mid"]) {
    const v = e[key];
    if (typeof v === "number" && Number.isFinite(v)) return v;
  }
  return null;
}
