"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { Check, LayoutGrid, Plus, Trash2 } from "lucide-react";

import { useUiStore } from "@/lib/store/ui";
import { useWorkspaceStore, type Workspace } from "@/lib/store/workspace";
import { cn } from "@/lib/utils";

export function WorkspaceMenu() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const router = useRouter();
  const pathname = usePathname();
  const { setTheme } = useTheme();

  const workspaces = useWorkspaceStore((s) => s.workspaces);
  const activeName = useWorkspaceStore((s) => s.activeName);
  const saveWorkspace = useWorkspaceStore((s) => s.saveWorkspace);
  const removeWorkspace = useWorkspaceStore((s) => s.removeWorkspace);
  const setActive = useWorkspaceStore((s) => s.setActive);

  const selectedSymbol = useUiStore((s) => s.selectedSymbol);
  const setSelectedSymbol = useUiStore((s) => s.setSelectedSymbol);
  const sidebarCollapsed = useUiStore((s) => s.sidebarCollapsed);
  const setSidebarCollapsed = useUiStore((s) => s.setSidebarCollapsed);

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, []);

  const apply = (ws: Workspace) => {
    setActive(ws.name);
    setSelectedSymbol(ws.selectedSymbol);
    setSidebarCollapsed(ws.sidebarCollapsed);
    if (ws.theme) setTheme(ws.theme);
    router.push(ws.route);
    setOpen(false);
  };

  const saveCurrent = () => {
    const name = window.prompt("Save current view as…");
    if (!name) return;
    saveWorkspace({
      name,
      route: pathname,
      selectedSymbol,
      sidebarCollapsed,
      theme: null,
    });
  };

  return (
    <div className="relative hidden md:block" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 rounded-md border border-border/60 px-2 py-1 text-xs text-muted-foreground hover:bg-muted"
      >
        <LayoutGrid className="size-3.5" />
        <span>{activeName ?? "Workspace"}</span>
      </button>
      {open && (
        <div className="absolute left-0 z-50 mt-1 w-56 overflow-hidden rounded-lg border border-border bg-popover p-1 shadow-xl">
          {workspaces.map((ws) => (
            <div
              key={ws.name}
              className={cn(
                "group flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-muted",
                ws.name === activeName && "text-foreground",
              )}
            >
              <button type="button" onClick={() => apply(ws)} className="flex flex-1 items-center gap-2 text-left">
                {ws.name === activeName ? (
                  <Check className="size-3.5 text-bull" />
                ) : (
                  <span className="size-3.5" />
                )}
                <span className="flex-1">{ws.name}</span>
                <span className="text-[10px] text-muted-foreground/60">{ws.route}</span>
              </button>
              <button
                type="button"
                onClick={() => removeWorkspace(ws.name)}
                className="opacity-0 transition-opacity group-hover:opacity-100"
                title="Remove"
              >
                <Trash2 className="size-3 text-muted-foreground hover:text-bear" />
              </button>
            </div>
          ))}
          <button
            type="button"
            onClick={saveCurrent}
            className="mt-1 flex w-full items-center gap-2 rounded-md border-t border-border px-2 py-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <Plus className="size-3.5" />
            Save current view…
          </button>
        </div>
      )}
    </div>
  );
}
