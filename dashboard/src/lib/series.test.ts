import { describe, expect, it } from "vitest";

import { getSeries, pushSeries, subscribeSeries } from "./series";

describe("series store", () => {
  it("accumulates, dedupes identical last, and caps length", () => {
    const key = `t-${Math.random()}`;
    pushSeries(key, 1);
    pushSeries(key, 2);
    pushSeries(key, 2); // identical to last -> ignored
    expect(getSeries(key)).toEqual([1, 2]);

    for (let i = 0; i < 50; i += 1) pushSeries(key, i + 3, 5);
    expect(getSeries(key)).toHaveLength(5);
  });

  it("ignores non-finite values", () => {
    const key = `n-${Math.random()}`;
    pushSeries(key, Number.NaN);
    expect(getSeries(key)).toEqual([]);
  });

  it("notifies subscribers until unsubscribed", () => {
    const key = `s-${Math.random()}`;
    let calls = 0;
    const unsubscribe = subscribeSeries(key, () => {
      calls += 1;
    });
    pushSeries(key, 1);
    pushSeries(key, 2);
    expect(calls).toBe(2);
    unsubscribe();
    pushSeries(key, 3);
    expect(calls).toBe(2);
  });
});
