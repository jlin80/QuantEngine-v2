"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity } from "lucide-react";

import { cn } from "@/lib/utils";
import { NAV } from "./nav-items";

export function Sidebar({
  collapsed,
  onNavigate,
}: {
  collapsed: boolean;
  onNavigate?: () => void;
}) {
  const pathname = usePathname();

  return (
    <div className="flex h-full flex-col gap-1 overflow-y-auto py-3">
      <Link
        href="/"
        onClick={onNavigate}
        className={cn(
          "mb-2 flex items-center gap-2 px-4 py-1.5",
          collapsed && "justify-center px-0",
        )}
      >
        <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
          <Activity className="size-4" />
        </span>
        {!collapsed && (
          <span className="flex flex-col leading-none">
            <span className="text-sm font-semibold">Quant Engine</span>
            <span className="text-[10px] tracking-wide text-muted-foreground uppercase">
              Control Center
            </span>
          </span>
        )}
      </Link>

      <nav className="flex flex-col gap-3 px-2">
        {NAV.map((section) => (
          <div key={section.title} className="flex flex-col gap-0.5">
            {!collapsed && (
              <span className="px-2 py-1 text-[10px] font-medium tracking-wider text-muted-foreground/70 uppercase">
                {section.title}
              </span>
            )}
            {section.items.map((item) => {
              const active =
                item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
              const Icon = item.icon;
              const base = cn(
                "group flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors",
                collapsed && "justify-center px-0",
              );

              if (!item.enabled) {
                return (
                  <div
                    key={item.href}
                    title={collapsed ? `${item.label} (soon)` : undefined}
                    className={cn(base, "cursor-default text-muted-foreground/40")}
                  >
                    <Icon className="size-4 shrink-0" />
                    {!collapsed && (
                      <>
                        <span className="flex-1 truncate">{item.label}</span>
                        <span className="rounded-sm border border-border/60 px-1 py-px text-[9px] tracking-wide uppercase">
                          soon
                        </span>
                      </>
                    )}
                  </div>
                );
              }

              return (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={onNavigate}
                  title={collapsed ? item.label : undefined}
                  className={cn(
                    base,
                    active
                      ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
                      : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground",
                  )}
                >
                  <Icon className="size-4 shrink-0" />
                  {!collapsed && <span className="flex-1 truncate">{item.label}</span>}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>

      <div className="mt-auto px-4 pt-3">
        {!collapsed && (
          <p className="text-[10px] leading-relaxed text-muted-foreground/60">
            Paper trading · Live disabled
          </p>
        )}
      </div>
    </div>
  );
}
