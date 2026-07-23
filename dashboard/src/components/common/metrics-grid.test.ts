import { describe, expect, it } from "vitest";

import { renderScalar } from "./metrics-grid";

describe("renderScalar", () => {
  it("formats primitives and dates", () => {
    expect(renderScalar(null)).toBe("—");
    expect(renderScalar(true)).toBe("Yes");
    expect(renderScalar(false)).toBe("No");
    expect(renderScalar(3)).toBe("3");
    expect(renderScalar(1.23456)).toBe("1.2346");
    expect(renderScalar("hello")).toBe("hello");
    expect(renderScalar("2026-07-18T00:00:00Z")).not.toBe("—");
    expect(renderScalar([1, 2, 3])).toBe("[3]");
  });
});
