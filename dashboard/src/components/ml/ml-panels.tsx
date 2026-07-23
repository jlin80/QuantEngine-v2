"use client";

import { Award, BrainCircuit, Layers, Sparkles, Workflow } from "lucide-react";

import { SectionCard } from "@/components/common/section-card";
import { DictTable, MetricsGrid } from "@/components/common/metrics-grid";
import { Async, EmptyState } from "@/components/common/states";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  useMlFeatures,
  useMlMeta,
  useMlModels,
  useMlRanking,
  useMlStatus,
} from "@/lib/api/hooks";

export function MlStatusCard() {
  const query = useMlStatus();
  return (
    <SectionCard title="ML status" icon={<BrainCircuit />}>
      <Async query={query} disabledLabel="Machine Learning disabled">
        {(d) => <MetricsGrid data={d} columns={2} />}
      </Async>
    </SectionCard>
  );
}

export function MlModelsCard() {
  const query = useMlModels();
  return (
    <SectionCard title="Model registry" icon={<Layers />} contentClassName="pt-0">
      <Async query={query} disabledLabel="Machine Learning disabled">
        {(d) => (
          <div className="space-y-4 pt-4">
            {d.active ? (
              <div className="rounded-lg border border-bull/30 bg-bull/5 p-3">
                <div className="mb-2 flex items-center gap-2 text-sm font-medium">
                  <Badge variant="outline" className="border-bull/40 text-bull">
                    active
                  </Badge>
                  <span>Active model</span>
                </div>
                <MetricsGrid data={d.active} columns={3} />
              </div>
            ) : (
              <EmptyState label="No active model" detail="Train a model to populate the registry." />
            )}
            {d.models.length > 0 && <DictTable rows={d.models} />}
          </div>
        )}
      </Async>
    </SectionCard>
  );
}

export function MlRankingCard() {
  const query = useMlRanking();
  return (
    <SectionCard title="Strategy ranking" icon={<Award />} contentClassName="pt-0">
      <Async
        query={query}
        disabledLabel="Machine Learning disabled"
        isEmpty={(d) => d.ranking.length === 0}
        emptyLabel="No ranking yet"
      >
        {(d) => (
          <ScrollArea className="max-h-[360px]">
            <DictTable rows={d.ranking} />
          </ScrollArea>
        )}
      </Async>
    </SectionCard>
  );
}

export function MlFeaturesCard() {
  const query = useMlFeatures();
  return (
    <SectionCard title="Feature store" icon={<Sparkles />} contentClassName="pt-0">
      <Async
        query={query}
        disabledLabel="Machine Learning disabled"
        isEmpty={(d) => d.features.length === 0}
        emptyLabel="No features registered"
      >
        {(d) => (
          <ScrollArea className="max-h-[360px]">
            <DictTable rows={d.features} />
          </ScrollArea>
        )}
      </Async>
    </SectionCard>
  );
}

export function MlMetaCard() {
  const query = useMlMeta();
  return (
    <SectionCard title="Meta strategy manager" icon={<Workflow />}>
      <Async query={query} disabledLabel="Machine Learning disabled">
        {(d) => <MetricsGrid data={d} columns={2} />}
      </Async>
    </SectionCard>
  );
}
