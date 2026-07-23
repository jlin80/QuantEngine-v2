/**
 * Central runtime config for the dashboard.
 *
 * The backend (FastAPI inside the engine) defaults to :8000 with CORS open to
 * http://localhost:3000. Both values can be overridden via NEXT_PUBLIC_* env.
 */

function stripTrailingSlash(url: string): string {
  return url.replace(/\/+$/, "");
}

function deriveWsUrl(apiBase: string): string {
  // http://host:8000 -> ws://host:8000/ws/events
  const ws = apiBase.replace(/^http/i, "ws");
  return `${stripTrailingSlash(ws)}/ws/events`;
}

export const API_BASE = stripTrailingSlash(
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000",
);

export const WS_URL =
  process.env.NEXT_PUBLIC_WS_URL ?? deriveWsUrl(API_BASE);

/** Symbols to surface by default until the market feed reports its own list. */
export const FALLBACK_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"] as const;
