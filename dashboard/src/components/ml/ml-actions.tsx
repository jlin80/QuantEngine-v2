"use client";

import { Loader2, Play, Radar, Workflow } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useMlDriftCheck, useMlMetaEvaluate, useMlTrain } from "@/lib/api/mutations";

export function MlActionBar() {
  const train = useMlTrain();
  const drift = useMlDriftCheck();
  const meta = useMlMetaEvaluate();

  return (
    <div className="flex flex-wrap gap-2">
      <Button size="sm" variant="outline" onClick={() => train.mutate()} disabled={train.isPending}>
        {train.isPending ? <Loader2 className="animate-spin" /> : <Play />}
        Train
      </Button>
      <Button size="sm" variant="outline" onClick={() => drift.mutate()} disabled={drift.isPending}>
        {drift.isPending ? <Loader2 className="animate-spin" /> : <Radar />}
        Drift check
      </Button>
      <Button size="sm" variant="outline" onClick={() => meta.mutate()} disabled={meta.isPending}>
        {meta.isPending ? <Loader2 className="animate-spin" /> : <Workflow />}
        Meta eval
      </Button>
    </div>
  );
}
