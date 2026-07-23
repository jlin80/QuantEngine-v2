"use client";

import { useState } from "react";

import { useUiStore } from "@/lib/store/ui";
import { cn } from "@/lib/utils";
import { Sidebar } from "./sidebar";
import { TopBar } from "./top-bar";

export function AppShell({ children }: { children: React.ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const collapsed = useUiStore((s) => s.sidebarCollapsed);

  return (
    <div className="flex min-h-screen">
      {/* Desktop sidebar */}
      <aside
        className={cn(
          "bg-sidebar hidden shrink-0 border-r border-border/60 md:block",
          collapsed ? "w-16" : "w-60",
        )}
      >
        <div className="sticky top-0 h-screen">
          <Sidebar collapsed={collapsed} />
        </div>
      </aside>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="md:hidden">
          <div
            className="fixed inset-0 z-40 bg-black/60"
            onClick={() => setMobileOpen(false)}
            aria-hidden
          />
          <aside className="bg-sidebar fixed inset-y-0 left-0 z-50 w-64 border-r border-border/60">
            <Sidebar collapsed={false} onNavigate={() => setMobileOpen(false)} />
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar onMenu={() => setMobileOpen(true)} />
        <main className="min-w-0 flex-1 p-4 md:p-6">{children}</main>
      </div>
    </div>
  );
}
