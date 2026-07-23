"use client";

import { motion } from "framer-motion";
import { Activity } from "lucide-react";

import { SectionCard } from "@/components/common/section-card";
import { Async } from "@/components/common/states";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useMarketTrades } from "@/lib/api/hooks";
import { fmtNum, fmtPrice, fmtTime } from "@/lib/format";
import { cn } from "@/lib/utils";

export function TradesTape({ symbol }: { symbol: string | null }) {
  const query = useMarketTrades(symbol, 60);
  return (
    <SectionCard title="Time & sales" icon={<Activity />} contentClassName="pt-2">
      <Async
        query={query}
        disabledLabel="Data engine disabled"
        isEmpty={(d) => d.trades.length === 0}
        emptyLabel="No trades"
        emptyDetail="Recent trades stream here."
      >
        {(d) => (
          <ScrollArea className="h-80">
            <ul className="pr-2 font-mono text-xs">
              {[...d.trades].reverse().map((t, i) => {
                const buy = t.side.toLowerCase() === "buy";
                return (
                  <motion.li
                    key={`${t.trade_id ?? i}-${t.local_ts ?? i}`}
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    className={cn(
                      "flex items-center justify-between gap-2 px-1.5 py-0.5",
                      buy ? "text-bull" : "text-bear",
                    )}
                  >
                    <span className="text-muted-foreground/70">{fmtTime(t.local_ts)}</span>
                    <span className="tnum">{fmtPrice(t.price)}</span>
                    <span className="tnum text-muted-foreground">{fmtNum(t.size, { decimals: 4 })}</span>
                  </motion.li>
                );
              })}
            </ul>
          </ScrollArea>
        )}
      </Async>
    </SectionCard>
  );
}
