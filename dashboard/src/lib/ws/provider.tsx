"use client";

/**
 * Wires the DashboardSocket into React: feeds the realtime store, throttles
 * query invalidation per domain, and raises alerts/toasts for risk events.
 */

import { createContext, useContext, useEffect, useMemo, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { WS_URL } from "@/lib/config";
import { useRealtimeStore } from "@/lib/store/realtime";
import { DashboardSocket } from "./client";
import {
  alertSeverityFor,
  eventPrice,
  eventSymbol,
  EventName,
  type BusEvent,
} from "./events";

type Domain = "execution" | "system" | "market";

const EXECUTION_EVENTS = new Set<string>([
  EventName.OrderCreated,
  EventName.OrderExecuted,
  EventName.OrderFilled,
  EventName.OrderRejected,
  EventName.PositionOpened,
  EventName.PositionModified,
  EventName.PositionClosed,
  EventName.StopMoved,
  EventName.TrailingUpdated,
  EventName.BreakEvenActivated,
  EventName.PaperTradeExecuted,
  EventName.TradeOpened,
  EventName.TradeClosed,
  EventName.RiskTriggered,
  EventName.KillSwitchTriggered,
  EventName.CircuitBreakerTriggered,
]);

const SYSTEM_EVENTS = new Set<string>([
  EventName.SystemStarted,
  EventName.SystemStopping,
  EventName.ModuleHealthChanged,
  EventName.ModuleFrozen,
  EventName.ConnectionLost,
  EventName.ConnectionRestored,
]);

function describe(e: BusEvent): string {
  const parts: string[] = [];
  const sym = eventSymbol(e);
  if (sym) parts.push(sym);
  for (const k of ["reason", "rule", "message", "detail", "module"]) {
    const v = e[k];
    if (typeof v === "string" && v) {
      parts.push(v);
      break;
    }
  }
  return parts.join(" · ") || e.event;
}

const WsContext = createContext<DashboardSocket | null>(null);

export function useSocket(): DashboardSocket | null {
  return useContext(WsContext);
}

export function WsProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const socket = useMemo(() => new DashboardSocket(WS_URL), []);
  const pendingRef = useRef<Set<Domain>>(new Set());
  const flushTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const scheduleInvalidate = (domain: Domain) => {
      pendingRef.current.add(domain);
      if (flushTimer.current) return;
      flushTimer.current = setTimeout(() => {
        const domains = pendingRef.current;
        pendingRef.current = new Set();
        flushTimer.current = null;
        for (const d of domains) {
          queryClient.invalidateQueries({ queryKey: [d] });
          if (d === "system") queryClient.invalidateQueries({ queryKey: ["health"] });
        }
      }, 600);
    };

    const offStatus = socket.onStatus((status) => {
      useRealtimeStore.getState().setStatus(status);
    });

    const offEvent = socket.onEvent((e) => {
      const rt = useRealtimeStore.getState();
      rt.pushEvent(e);

      if (e.event === EventName.PriceUpdated) {
        const symbol = eventSymbol(e);
        const price = eventPrice(e);
        if (symbol && price !== null) rt.pushPrice(symbol, price, e.occurred_at);
      }

      const severity = alertSeverityFor(e.event);
      if (severity) {
        const message = describe(e);
        rt.pushAlert({
          id: e.event_id,
          event: e.event,
          severity,
          message,
          source: e.source,
          at: e.occurred_at,
        });
        if (severity === "critical") {
          toast.error(e.event, { description: message });
        }
      }

      if (EXECUTION_EVENTS.has(e.event)) scheduleInvalidate("execution");
      if (SYSTEM_EVENTS.has(e.event)) scheduleInvalidate("system");
    });

    socket.connect();

    return () => {
      offStatus();
      offEvent();
      if (flushTimer.current) clearTimeout(flushTimer.current);
      socket.close();
    };
  }, [socket, queryClient]);

  return <WsContext.Provider value={socket}>{children}</WsContext.Provider>;
}
