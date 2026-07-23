"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { CornerDownLeft, Moon, Search, Sun } from "lucide-react";

import { NAV, type NavIcon } from "@/components/layout/nav-items";
import { cn } from "@/lib/utils";

interface Command {
  id: string;
  label: string;
  hint?: string;
  icon: NavIcon;
  run: () => void;
}

export function CommandPalette() {
  const router = useRouter();
  const { setTheme, resolvedTheme } = useTheme();
  const [open, setOpen] = useState(false);
  const [queryText, setQueryText] = useState("");
  const [index, setIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
      } else if (e.key === "Escape") {
        setOpen(false);
      }
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("open-command-palette", onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("open-command-palette", onOpen);
    };
  }, []);

  const commands = useMemo<Command[]>(() => {
    const navCommands: Command[] = NAV.flatMap((section) =>
      section.items
        .filter((item) => item.enabled)
        .map((item) => ({
          id: `nav:${item.href}`,
          label: item.label,
          hint: section.title,
          icon: item.icon,
          run: () => router.push(item.href),
        })),
    );
    const isDark = resolvedTheme === "dark";
    navCommands.push({
      id: "theme",
      label: `Switch to ${isDark ? "light" : "dark"} theme`,
      hint: "Appearance",
      icon: isDark ? Sun : Moon,
      run: () => setTheme(isDark ? "light" : "dark"),
    });
    return navCommands;
  }, [router, resolvedTheme, setTheme]);

  const filtered = useMemo(() => {
    const q = queryText.trim().toLowerCase();
    if (!q) return commands;
    return commands.filter((c) => c.label.toLowerCase().includes(q));
  }, [commands, queryText]);

  const run = (cmd: Command) => {
    cmd.run();
    setOpen(false);
    setQueryText("");
    setIndex(0);
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center bg-black/50 pt-[15vh]"
      onClick={() => setOpen(false)}
    >
      <div
        className="w-full max-w-lg overflow-hidden rounded-xl border border-border bg-popover shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-border px-3">
          <Search className="size-4 text-muted-foreground" />
          <input
            ref={inputRef}
            autoFocus
            value={queryText}
            onChange={(e) => {
              setQueryText(e.target.value);
              setIndex(0);
            }}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setIndex((i) => Math.min(i + 1, filtered.length - 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setIndex((i) => Math.max(i - 1, 0));
              } else if (e.key === "Enter" && filtered[index]) {
                run(filtered[index]);
              }
            }}
            placeholder="Jump to…"
            className="h-11 flex-1 bg-transparent text-sm outline-none"
          />
          <kbd className="rounded border border-border px-1 text-[10px] text-muted-foreground">esc</kbd>
        </div>
        <ul className="max-h-72 overflow-y-auto p-1">
          {filtered.length === 0 && (
            <li className="px-3 py-6 text-center text-sm text-muted-foreground">No results</li>
          )}
          {filtered.map((cmd, i) => {
            const Icon = cmd.icon;
            return (
              <li key={cmd.id}>
                <button
                  type="button"
                  onMouseEnter={() => setIndex(i)}
                  onClick={() => run(cmd)}
                  className={cn(
                    "flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-sm",
                    i === index ? "bg-muted text-foreground" : "text-muted-foreground",
                  )}
                >
                  <Icon className="size-4" />
                  <span className="flex-1 text-left">{cmd.label}</span>
                  {cmd.hint && <span className="text-xs text-muted-foreground/60">{cmd.hint}</span>}
                  {i === index && <CornerDownLeft className="size-3.5" />}
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
