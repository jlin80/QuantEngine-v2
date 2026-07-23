/** Small persisted UI store (sidebar + selected market symbol). */

import { create } from "zustand";
import { persist } from "zustand/middleware";

interface UiState {
  sidebarCollapsed: boolean;
  selectedSymbol: string | null;
  toggleSidebar: () => void;
  setSidebarCollapsed: (v: boolean) => void;
  setSelectedSymbol: (symbol: string | null) => void;
}

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      selectedSymbol: null,
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),
      setSelectedSymbol: (symbol) => set({ selectedSymbol: symbol }),
    }),
    { name: "qe-ui" },
  ),
);
