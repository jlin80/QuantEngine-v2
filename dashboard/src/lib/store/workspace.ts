/**
 * Workspace system: named saved views. Each workspace captures the current
 * route plus view preferences (selected symbol, sidebar, theme) so the operator
 * can switch instantly between specialized layouts (Trading, Monitoring, …).
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface Workspace {
  name: string;
  route: string;
  selectedSymbol: string | null;
  sidebarCollapsed: boolean;
  theme: string | null;
}

const PRESETS: Workspace[] = [
  { name: "Monitoring", route: "/", selectedSymbol: null, sidebarCollapsed: false, theme: "dark" },
  { name: "Trading", route: "/operations", selectedSymbol: null, sidebarCollapsed: false, theme: "dark" },
  { name: "Order Flow", route: "/orderflow", selectedSymbol: null, sidebarCollapsed: true, theme: "dark" },
  { name: "Backtesting", route: "/backtesting", selectedSymbol: null, sidebarCollapsed: false, theme: "dark" },
  { name: "Machine Learning", route: "/ml", selectedSymbol: null, sidebarCollapsed: false, theme: "dark" },
  { name: "Risk", route: "/alerts", selectedSymbol: null, sidebarCollapsed: false, theme: "dark" },
];

interface WorkspaceState {
  workspaces: Workspace[];
  activeName: string | null;
  saveWorkspace: (ws: Workspace) => void;
  removeWorkspace: (name: string) => void;
  setActive: (name: string | null) => void;
}

export const useWorkspaceStore = create<WorkspaceState>()(
  persist(
    (set) => ({
      workspaces: PRESETS,
      activeName: null,
      saveWorkspace: (ws) =>
        set((state) => {
          const rest = state.workspaces.filter((w) => w.name !== ws.name);
          return { workspaces: [...rest, ws], activeName: ws.name };
        }),
      removeWorkspace: (name) =>
        set((state) => ({
          workspaces: state.workspaces.filter((w) => w.name !== name),
          activeName: state.activeName === name ? null : state.activeName,
        })),
      setActive: (name) => set({ activeName: name }),
    }),
    { name: "qe-workspaces" },
  ),
);
