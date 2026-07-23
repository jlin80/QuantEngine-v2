/**
 * Resilient WebSocket client for the engine event stream.
 *
 * - Auto-reconnect with exponential backoff + jitter.
 * - Receive-only (the backend accepts no inbound commands today).
 * - Fan-out to multiple event/status subscribers.
 */

import type { BusEvent } from "./events";

export type SocketStatus = "connecting" | "open" | "closed";

type EventHandler = (e: BusEvent) => void;
type StatusHandler = (s: SocketStatus) => void;

const MAX_BACKOFF_MS = 15_000;
const BASE_BACKOFF_MS = 500;

export class DashboardSocket {
  private ws: WebSocket | null = null;
  private readonly eventHandlers = new Set<EventHandler>();
  private readonly statusHandlers = new Set<StatusHandler>();
  private retries = 0;
  private stopped = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private _status: SocketStatus = "closed";

  constructor(private readonly url: string) {}

  get status(): SocketStatus {
    return this._status;
  }

  onEvent(handler: EventHandler): () => void {
    this.eventHandlers.add(handler);
    return () => this.eventHandlers.delete(handler);
  }

  onStatus(handler: StatusHandler): () => void {
    this.statusHandlers.add(handler);
    handler(this._status);
    return () => this.statusHandlers.delete(handler);
  }

  connect(): void {
    if (typeof window === "undefined") return; // never on the server
    this.stopped = false;
    this.open();
  }

  private open(): void {
    this.clearTimer();
    this.setStatus("connecting");
    let socket: WebSocket;
    try {
      socket = new WebSocket(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = socket;

    socket.onopen = () => {
      this.retries = 0;
      this.setStatus("open");
    };

    socket.onmessage = (ev: MessageEvent) => {
      let parsed: BusEvent;
      try {
        parsed = JSON.parse(ev.data as string) as BusEvent;
      } catch {
        return;
      }
      for (const handler of this.eventHandlers) {
        try {
          handler(parsed);
        } catch {
          /* isolate handler errors */
        }
      }
    };

    socket.onclose = () => {
      this.ws = null;
      this.setStatus("closed");
      if (!this.stopped) this.scheduleReconnect();
    };

    socket.onerror = () => {
      // onclose fires next and drives the reconnect.
      socket.close();
    };
  }

  private scheduleReconnect(): void {
    if (this.stopped) return;
    const backoff = Math.min(MAX_BACKOFF_MS, BASE_BACKOFF_MS * 2 ** this.retries);
    const jitter = Math.random() * 0.3 * backoff;
    this.retries += 1;
    this.clearTimer();
    this.reconnectTimer = setTimeout(() => this.open(), backoff + jitter);
  }

  private setStatus(status: SocketStatus): void {
    this._status = status;
    for (const handler of this.statusHandlers) {
      try {
        handler(status);
      } catch {
        /* isolate */
      }
    }
  }

  private clearTimer(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  close(): void {
    this.stopped = true;
    this.clearTimer();
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.onerror = null;
      this.ws.close();
      this.ws = null;
    }
    this.setStatus("closed");
  }
}
