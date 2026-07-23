import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DashboardSocket, type SocketStatus } from "./client";
import type { BusEvent } from "./events";

class MockWebSocket {
  static last: MockWebSocket | null = null;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  readonly url: string;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.last = this;
  }

  close(): void {
    this.onclose?.();
  }
}

describe("DashboardSocket", () => {
  beforeEach(() => {
    MockWebSocket.last = null;
    vi.stubGlobal("WebSocket", MockWebSocket as unknown as typeof WebSocket);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("transitions status and forwards parsed events", () => {
    const socket = new DashboardSocket("ws://x/ws/events");
    const statuses: SocketStatus[] = [];
    const received: BusEvent[] = [];
    socket.onStatus((s) => statuses.push(s));
    socket.onEvent((e) => received.push(e));

    socket.connect();
    expect(MockWebSocket.last).not.toBeNull();
    expect(statuses).toContain("connecting");

    MockWebSocket.last?.onopen?.();
    expect(socket.status).toBe("open");

    MockWebSocket.last?.onmessage?.({
      data: JSON.stringify({
        event: "PriceUpdated",
        event_id: "1",
        occurred_at: "2026-07-18T00:00:00Z",
        source: "market",
        symbol: "BTCUSDT",
        price: 65000,
      }),
    });
    expect(received).toHaveLength(1);
    expect(received[0].event).toBe("PriceUpdated");

    socket.close();
    expect(socket.status).toBe("closed");
  });

  it("ignores malformed frames without throwing", () => {
    const socket = new DashboardSocket("ws://x/ws/events");
    const received: BusEvent[] = [];
    socket.onEvent((e) => received.push(e));
    socket.connect();
    MockWebSocket.last?.onmessage?.({ data: "not-json{" });
    expect(received).toHaveLength(0);
    socket.close();
  });
});
