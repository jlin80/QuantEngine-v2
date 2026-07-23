/**
 * Tiny in-memory time series store for session-lived sparklines, exposed as a
 * subscribable external store (works with useSyncExternalStore). Values persist
 * across route changes within a session (module-level), reset on full reload.
 */

const EMPTY: number[] = [];
const store = new Map<string, number[]>();
const listeners = new Map<string, Set<() => void>>();

export function pushSeries(key: string, value: number, cap = 120): void {
  if (!Number.isFinite(value)) return;
  const arr = store.get(key) ?? EMPTY;
  if (arr.length > 0 && arr[arr.length - 1] === value) return;
  store.set(key, [...arr, value].slice(-cap));
  listeners.get(key)?.forEach((l) => l());
}

export function getSeries(key: string): number[] {
  return store.get(key) ?? EMPTY;
}

export function subscribeSeries(key: string, cb: () => void): () => void {
  let set = listeners.get(key);
  if (!set) {
    set = new Set();
    listeners.set(key, set);
  }
  set.add(cb);
  return () => {
    set.delete(cb);
  };
}
