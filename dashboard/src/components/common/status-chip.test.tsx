import "@testing-library/jest-dom/vitest";

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusChip, statusTone } from "./status-chip";

describe("statusTone", () => {
  it("classifies common status strings", () => {
    expect(statusTone("healthy")).toBe("bull");
    expect(statusTone("connected")).toBe("bull");
    expect(statusTone("degraded")).toBe("warn");
    expect(statusTone("disconnected")).toBe("bear");
    expect(statusTone("something-else")).toBe("muted");
  });
});

describe("StatusChip", () => {
  it("renders a title-cased label and the raw value", () => {
    render(<StatusChip label="redis_cache" value="connected" />);
    expect(screen.getByText("Redis Cache")).toBeInTheDocument();
    expect(screen.getByText("connected")).toBeInTheDocument();
  });
});
