"use client";

import { useEffect, useSyncExternalStore } from "react";

import { getSeries, pushSeries, subscribeSeries } from "@/lib/series";

/**
 * Accumulate a scalar into a session-lived series and return it. The effect only
 * updates the external store (no React setState); the component re-renders via
 * useSyncExternalStore when the store notifies.
 */
export function useLiveSeries(
  key: string,
  value: number | undefined | null,
  cap = 120,
): number[] {
  useEffect(() => {
    if (value === undefined || value === null || !Number.isFinite(value)) return;
    pushSeries(key, value, cap);
  }, [key, value, cap]);

  return useSyncExternalStore(
    (cb) => subscribeSeries(key, cb),
    () => getSeries(key),
    () => getSeries(key),
  );
}
