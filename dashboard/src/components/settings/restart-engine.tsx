"use client";

import { useState } from "react";
import { Loader2, Power, TriangleAlert } from "lucide-react";

import { SectionCard } from "@/components/common/section-card";
import { Button } from "@/components/ui/button";
import { useEngineRestart } from "@/lib/api/mutations";

/**
 * Orderly engine restart. Two-step on purpose: the engine simply stops and the
 * watchdog relaunches it, so a stray click would take live trading down for a
 * few seconds. The backend refuses outright while positions are open.
 */
export function RestartEngineCard() {
  const [armed, setArmed] = useState(false);
  const restart = useEngineRestart();

  return (
    <SectionCard title="Engine" icon={<Power />}>
      <div className="space-y-3">
        <p className="text-sm text-muted-foreground">
          Settings marked <span className="text-foreground">restart required</span> are only read
          at startup. Restarting stops the engine; the watchdog brings it back in a few seconds.
        </p>
        <p className="flex items-start gap-2 text-xs text-muted-foreground">
          <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-warn" />
          <span>
            Refused while any position is open — a restart would leave them without trailing,
            break-even or regime exit until the engine is back.
          </span>
        </p>

        {!armed ? (
          <Button size="sm" variant="outline" onClick={() => setArmed(true)}>
            <Power />
            Restart engine
          </Button>
        ) : (
          <div className="flex flex-wrap items-center gap-2 rounded-md border border-warn/40 bg-warn/5 p-2">
            <span className="text-sm">Restart the engine now?</span>
            <Button
              size="sm"
              variant="destructive"
              disabled={restart.isPending}
              onClick={() =>
                restart.mutate(
                  { confirm: true, reason: "dashboard" },
                  { onSettled: () => setArmed(false) },
                )
              }
            >
              {restart.isPending ? <Loader2 className="animate-spin" /> : <Power />}
              Yes, restart
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setArmed(false)}
              disabled={restart.isPending}
            >
              Cancel
            </Button>
          </div>
        )}
      </div>
    </SectionCard>
  );
}
