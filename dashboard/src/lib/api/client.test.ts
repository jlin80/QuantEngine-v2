import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiGet, isNotEnabled, isUnreachable } from "./client";

function mockFetch(opts: {
  ok?: boolean;
  status?: number;
  json?: unknown;
}): typeof fetch {
  return vi.fn(async () => ({
    ok: opts.ok ?? true,
    status: opts.status ?? 200,
    statusText: "",
    json: async () => opts.json ?? {},
  })) as unknown as typeof fetch;
}

describe("apiGet", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("returns parsed JSON on 2xx", async () => {
    vi.stubGlobal("fetch", mockFetch({ json: { value: 42 } }));
    const data = await apiGet<{ value: number }>("/api/x");
    expect(data.value).toBe(42);
  });

  it("throws ApiError flagged notEnabled on 503", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({ ok: false, status: 503, json: { detail: "Data Engine not enabled" } }),
    );
    const err = await apiGet("/api/x").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(isNotEnabled(err)).toBe(true);
    expect((err as ApiError).detail).toBe("Data Engine not enabled");
  });

  it("wraps network failures as ApiError status 0", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("boom");
      }) as unknown as typeof fetch,
    );
    const err = await apiGet("/api/x").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(isUnreachable(err)).toBe(true);
  });
});
