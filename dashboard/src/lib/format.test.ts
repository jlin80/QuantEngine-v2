import { describe, expect, it } from "vitest";

import { fmtDuration, fmtMoney, fmtNum, fmtPct, fmtRatioPct, pnlClass, titleCase } from "./format";

describe("format", () => {
  it("formats money with sign and dash fallback", () => {
    expect(fmtMoney(1234.5)).toBe("$1,234.50");
    expect(fmtMoney(-5)).toBe("-$5.00");
    expect(fmtMoney(5, { sign: true })).toBe("+$5.00");
    expect(fmtMoney(null)).toBe("—");
    expect(fmtMoney(Number.NaN)).toBe("—");
  });

  it("formats percentages (already-percent values)", () => {
    expect(fmtPct(12.5)).toBe("12.50%");
    expect(fmtPct(3, { sign: true })).toBe("+3.00%");
    expect(fmtPct(undefined)).toBe("—");
  });

  it("formats fractions as percentages", () => {
    expect(fmtRatioPct(0.6)).toBe("60.0%");
    expect(fmtRatioPct(null)).toBe("—");
  });

  it("formats numbers with optional sign", () => {
    expect(fmtNum(2, { sign: true })).toBe("+2.00");
    expect(fmtNum(undefined)).toBe("—");
  });

  it("maps pnl to tone classes", () => {
    expect(pnlClass(1)).toContain("bull");
    expect(pnlClass(-1)).toContain("bear");
    expect(pnlClass(0)).toContain("muted");
  });

  it("formats durations", () => {
    expect(fmtDuration(45)).toBe("45s");
    expect(fmtDuration(90)).toBe("1m 30s");
    expect(fmtDuration(null)).toBe("—");
  });

  it("title-cases snake/kebab", () => {
    expect(titleCase("redis_cache")).toBe("Redis Cache");
  });
});
