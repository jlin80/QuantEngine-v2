"use client";

import { Bell, Menu, PanelLeft, Search } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { StatusDot } from "@/components/common/connection-dot";
import { ThemeToggle } from "@/components/common/theme-toggle";
import { WorkspaceMenu } from "@/components/layout/workspace-menu";
import { useExecutionStatus, useSystemInfo, useSystemStatus } from "@/lib/api/hooks";
import { useRealtimeStore } from "@/lib/store/realtime";
import { useUiStore } from "@/lib/store/ui";
import { cn } from "@/lib/utils";
import type { SocketStatus } from "@/lib/ws/client";

const WS_LABEL: Record<SocketStatus, string> = {
  open: "Live",
  connecting: "Connecting",
  closed: "Offline",
};

function healthTone(status: string | undefined): string {
  if (status === "healthy") return "bg-bull";
  if (status === "degraded") return "bg-warn";
  if (!status) return "bg-muted-foreground/40";
  return "bg-bear";
}

export function TopBar({ onMenu }: { onMenu: () => void }) {
  const status = useRealtimeStore((s) => s.status);
  const alertCount = useRealtimeStore((s) => s.alerts.length);
  const toggleSidebar = useUiStore((s) => s.toggleSidebar);

  const info = useSystemInfo();
  const health = useSystemStatus();
  const exec = useExecutionStatus();

  const env = info.data?.environment;
  const mode = exec.data?.mode ?? "paper";
  const healthStatus = health.data?.status;

  return (
    <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center gap-2 border-b border-border/60 bg-background/80 px-3 backdrop-blur md:px-5">
      <button
        type="button"
        aria-label="Open navigation"
        onClick={onMenu}
        className="inline-flex size-8 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground md:hidden"
      >
        <Menu className="size-4" />
      </button>
      <button
        type="button"
        aria-label="Collapse sidebar"
        onClick={toggleSidebar}
        className="hidden size-8 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground md:inline-flex"
      >
        <PanelLeft className="size-4" />
      </button>

      <WorkspaceMenu />

      <button
        type="button"
        onClick={() => window.dispatchEvent(new CustomEvent("open-command-palette"))}
        className="hidden items-center gap-2 rounded-md border border-border/60 px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted sm:flex"
      >
        <Search className="size-3.5" />
        <span>Search</span>
        <kbd className="rounded border border-border px-1 text-[10px]">⌘K</kbd>
      </button>

      <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
        {env && (
          <Badge variant="outline" className="hidden capitalize sm:inline-flex">
            {env}
          </Badge>
        )}

        <Badge variant="secondary" className="uppercase">
          {mode}
        </Badge>
        <Badge variant="outline" className="border-bear/40 text-bear hidden sm:inline-flex">
          Live off
        </Badge>

        <Tooltip>
          <TooltipTrigger
            render={
              <span className="inline-flex items-center gap-1.5 rounded-md border border-border/60 px-2 py-1 text-xs text-muted-foreground">
                <StatusDot status={status} />
                <span className="hidden sm:inline">{WS_LABEL[status]}</span>
              </span>
            }
          />
          <TooltipContent>WebSocket stream: {WS_LABEL[status]}</TooltipContent>
        </Tooltip>

        <Tooltip>
          <TooltipTrigger
            render={
              <span className="inline-flex size-8 items-center justify-center rounded-md">
                <span className={cn("size-2 rounded-full", healthTone(healthStatus))} />
              </span>
            }
          />
          <TooltipContent>Engine health: {healthStatus ?? "unknown"}</TooltipContent>
        </Tooltip>

        <Tooltip>
          <TooltipTrigger
            render={
              <span className="relative inline-flex size-8 items-center justify-center rounded-md text-muted-foreground">
                <Bell className="size-4" />
                {alertCount > 0 && (
                  <span className="bg-bear text-background absolute -top-0.5 -right-0.5 flex min-w-4 items-center justify-center rounded-full px-1 text-[9px] font-semibold">
                    {alertCount > 99 ? "99+" : alertCount}
                  </span>
                )}
              </span>
            }
          />
          <TooltipContent>{alertCount} active alerts</TooltipContent>
        </Tooltip>

        <ThemeToggle />
      </div>
    </header>
  );
}
