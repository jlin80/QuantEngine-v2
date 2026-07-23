"use client";

import { motion } from "framer-motion";
import { ListOrdered } from "lucide-react";

import { SectionCard } from "@/components/common/section-card";
import { EmptyState } from "@/components/common/states";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useRealtimeStore } from "@/lib/store/realtime";
import { fmtAgo, fmtPrice } from "@/lib/format";
import { EventName, eventSymbol } from "@/lib/ws/events";
import type { BusEvent } from "@/lib/ws/events";
import { cn } from "@/lib/utils";

const ORDER_EVENTS = new Set<string>([
  EventName.OrderCreated,
  EventName.OrderExecuted,
  EventName.OrderFilled,
  EventName.OrderRejected,
]);

function tone(event: string): string {
  if (event === EventName.OrderRejected) return "bg-bear";
  if (event === EventName.OrderExecuted || event === EventName.OrderFilled) return "bg-bull";
  return "bg-muted-foreground/50";
}

function num(e: BusEvent, key: string): number | null {
  const v = e[key];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function str(e: BusEvent, key: string): string | null {
  const v = e[key];
  return typeof v === "string" && v ? v : null;
}

export function OrdersStreamCard() {
  const events = useRealtimeStore((s) => s.events);
  const orders = events.filter((e) => ORDER_EVENTS.has(e.event)).slice(0, 40);

  return (
    <SectionCard title="Order flow" icon={<ListOrdered />} contentClassName="pt-2">
      {orders.length === 0 ? (
        <EmptyState label="No order activity" detail="Order events stream here in real time." />
      ) : (
        <ScrollArea className="h-72">
          <ul className="space-y-1 pr-3">
            {orders.map((e) => {
              const price = num(e, "price") ?? num(e, "limit_price");
              const side = str(e, "side");
              const reason = str(e, "reason");
              return (
                <motion.li
                  key={e.event_id}
                  initial={{ opacity: 0, y: -3 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-muted/50"
                >
                  <span className={cn("size-1.5 shrink-0 rounded-full", tone(e.event))} />
                  <span className="font-medium">{e.event.replace("Order", "")}</span>
                  {eventSymbol(e) && <span>{eventSymbol(e)}</span>}
                  {side && <span className="text-muted-foreground uppercase">{side}</span>}
                  {price !== null && <span className="tnum text-muted-foreground">{fmtPrice(price)}</span>}
                  {reason && (
                    <span className="text-bear/80 truncate">{reason}</span>
                  )}
                  <span className="ml-auto shrink-0 text-muted-foreground/70">
                    {fmtAgo(e.occurred_at)}
                  </span>
                </motion.li>
              );
            })}
          </ul>
        </ScrollArea>
      )}
    </SectionCard>
  );
}
