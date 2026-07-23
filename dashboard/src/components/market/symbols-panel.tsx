"use client";

import { List } from "lucide-react";

import { SectionCard } from "@/components/common/section-card";
import { Async } from "@/components/common/states";
import { useSymbols } from "@/lib/api/hooks";
import type { MarketState } from "@/lib/api/types";
import { useRealtimeStore } from "@/lib/store/realtime";
import { useUiStore } from "@/lib/store/ui";
import { fmtPrice } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useActiveSymbol } from "./use-active-symbol";

function SymbolRow({
  sym,
  state,
  active,
  onSelect,
}: {
  sym: string;
  state: MarketState;
  active: boolean;
  onSelect: (s: string) => void;
}) {
  const price = useRealtimeStore((s) => s.lastPrices[sym]?.price);
  return (
    <button
      type="button"
      onClick={() => onSelect(sym)}
      className={cn(
        "flex w-full items-center justify-between rounded-md px-2.5 py-2 text-sm transition-colors",
        active ? "bg-sidebar-accent text-foreground" : "hover:bg-muted/50",
      )}
    >
      <span className="flex items-center gap-2">
        <span
          className={cn("size-1.5 rounded-full", state.connected ? "bg-bull" : "bg-bear")}
          title={state.connected ? "connected" : "disconnected"}
        />
        <span className="font-medium">{sym}</span>
      </span>
      <span className="tnum text-muted-foreground">
        {price !== undefined ? fmtPrice(price) : "—"}
      </span>
    </button>
  );
}

export function SymbolsPanel() {
  const query = useSymbols();
  const active = useActiveSymbol();
  const setSelected = useUiStore((s) => s.setSelectedSymbol);

  return (
    <SectionCard title="Symbols" icon={<List />} contentClassName="pt-2">
      <Async
        query={query}
        disabledLabel="Data engine disabled"
        isEmpty={(d) => Object.keys(d.symbols).length === 0}
        emptyLabel="No symbols"
        emptyDetail="The data engine has no subscribed symbols yet."
      >
        {(d) => (
          <div className="flex flex-col gap-0.5">
            {Object.entries(d.symbols).map(([sym, state]) => (
              <SymbolRow
                key={sym}
                sym={sym}
                state={state}
                active={sym === active}
                onSelect={setSelected}
              />
            ))}
          </div>
        )}
      </Async>
    </SectionCard>
  );
}
