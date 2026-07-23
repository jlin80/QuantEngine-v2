/**
 * Thin typed fetch wrapper around the engine's read-only REST API.
 *
 * Many subsystems boot disabled in dev, so the API answers 503 "... not
 * enabled" frequently. That is surfaced as `ApiError.notEnabled` so panels can
 * render a calm "subsystem off" state instead of a scary error.
 */

import { API_BASE } from "@/lib/config";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly notEnabled: boolean;

  constructor(status: number, detail: string) {
    super(detail || `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    // 503 from these routes means "subsystem disabled", not a real failure.
    this.notEnabled = status === 503;
  }
}

export type QueryValue = string | number | boolean | undefined | null;

function buildUrl(path: string, params?: Record<string, QueryValue>): string {
  const url = new URL(`${API_BASE}${path}`);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null) {
        url.searchParams.set(key, String(value));
      }
    }
  }
  return url.toString();
}

export async function apiGet<T>(
  path: string,
  params?: Record<string, QueryValue>,
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(buildUrl(path, params), {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
  } catch (err) {
    // Network error / engine not running.
    throw new ApiError(0, err instanceof Error ? err.message : "Network error");
  }

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }

  return (await res.json()) as T;
}

export async function apiSend<T>(
  method: "POST" | "PATCH" | "PUT" | "DELETE",
  path: string,
  body?: unknown,
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(buildUrl(path), {
      method,
      headers: {
        Accept: "application/json",
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      cache: "no-store",
    });
  } catch (err) {
    throw new ApiError(0, err instanceof Error ? err.message : "Network error");
  }

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const parsed = (await res.json()) as { detail?: string };
      if (parsed?.detail) detail = parsed.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }

  // Some endpoints return 204 / empty bodies.
  const text = await res.text();
  return (text ? JSON.parse(text) : {}) as T;
}

export function isNotEnabled(err: unknown): boolean {
  return err instanceof ApiError && err.notEnabled;
}

export function isUnreachable(err: unknown): boolean {
  return err instanceof ApiError && err.status === 0;
}
