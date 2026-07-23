"use client";

import { useSymbols } from "@/lib/api/hooks";
import { useUiStore } from "@/lib/store/ui";

/** Resolve the active market symbol: explicit selection, else first available. */
export function useActiveSymbol(): string | null {
  const selected = useUiStore((s) => s.selectedSymbol);
  const query = useSymbols();
  const first = query.data ? (Object.keys(query.data.symbols)[0] ?? null) : null;
  return selected ?? first;
}
