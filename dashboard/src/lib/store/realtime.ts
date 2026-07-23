/**
 * Live state fed by the WebSocket stream. Kept intentionally small: the source
 * of truth for portfolio/positions stays in TanStack Query (REST); the socket
 * drives prices, a rolling event log, and an alerts feed, and triggers query
 * invalidation from the WsProvider.
 */

import { create } from "zustand";

import type { SocketStatus } from "@/lib/ws/client";
import type { AlertSeverity, BusEvent } from "@/lib/ws/events";

const MAX_EVENTS = 250;
const MAX_ALERTS = 100;

export interface PricePoint {
  price: number;
  at: string;
}

export interface AlertItem {
  id: string;
  event: string;
  severity: AlertSeverity;
  message: string;
  source: string;
  at: string;
}

interface RealtimeState {
  status: SocketStatus;
  lastPrices: Record<string, PricePoint>;
  events: BusEvent[];
  alerts: AlertItem[];
  eventCount: number;
  setStatus: (status: SocketStatus) => void;
  pushPrice: (symbol: string, price: number, at: string) => void;
  pushEvent: (event: BusEvent) => void;
  pushAlert: (alert: AlertItem) => void;
  clearAlerts: () => void;
}

export const useRealtimeStore = create<RealtimeState>((set) => ({
  status: "closed",
  lastPrices: {},
  events: [],
  alerts: [],
  eventCount: 0,
  setStatus: (status) => set({ status }),
  pushPrice: (symbol, price, at) =>
    set((state) => ({
      lastPrices: { ...state.lastPrices, [symbol]: { price, at } },
    })),
  pushEvent: (event) =>
    set((state) => ({
      events: [event, ...state.events].slice(0, MAX_EVENTS),
      eventCount: state.eventCount + 1,
    })),
  pushAlert: (alert) =>
    set((state) => ({
      alerts: [alert, ...state.alerts].slice(0, MAX_ALERTS),
    })),
  clearAlerts: () => set({ alerts: [] }),
}));
