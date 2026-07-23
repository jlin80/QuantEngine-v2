import { beforeEach, describe, expect, it } from "vitest";

import { useWorkspaceStore } from "./workspace";

describe("workspace store", () => {
  beforeEach(() => {
    useWorkspaceStore.setState({ activeName: null });
  });

  it("ships with preset workspaces", () => {
    const names = useWorkspaceStore.getState().workspaces.map((w) => w.name);
    expect(names).toContain("Trading");
    expect(names).toContain("Monitoring");
  });

  it("saves a workspace (upsert) and marks it active", () => {
    useWorkspaceStore.getState().saveWorkspace({
      name: "Custom",
      route: "/market",
      selectedSymbol: "BTCUSDT",
      sidebarCollapsed: true,
      theme: null,
    });
    const state = useWorkspaceStore.getState();
    expect(state.activeName).toBe("Custom");
    expect(state.workspaces.find((w) => w.name === "Custom")?.route).toBe("/market");
  });

  it("removes a workspace", () => {
    useWorkspaceStore.getState().saveWorkspace({
      name: "Temp",
      route: "/",
      selectedSymbol: null,
      sidebarCollapsed: false,
      theme: null,
    });
    useWorkspaceStore.getState().removeWorkspace("Temp");
    const names = useWorkspaceStore.getState().workspaces.map((w) => w.name);
    expect(names).not.toContain("Temp");
  });
});
