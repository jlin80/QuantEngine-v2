"use client";

import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  LineStyle,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";

import type { Candle } from "@/lib/api/types";

const UP = "#2ebd85";
const DOWN = "#e15b64";
const GRID = "rgba(148,163,184,0.06)";
const AXIS = "rgba(148,163,184,0.15)";
const TEXT = "rgba(148,163,184,0.85)";

export function PriceChart({
  candles,
  livePrice,
  height = 380,
}: {
  candles: Candle[];
  livePrice: number | null;
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const priceLineRef = useRef<IPriceLine | null>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: TEXT,
        fontFamily: "inherit",
        attributionLogo: false,
      },
      grid: { vertLines: { color: GRID }, horzLines: { color: GRID } },
      rightPriceScale: { borderColor: AXIS },
      timeScale: { borderColor: AXIS, timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 },
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: UP,
      downColor: DOWN,
      borderVisible: false,
      wickUpColor: UP,
      wickDownColor: DOWN,
    });

    chartRef.current = chart;
    seriesRef.current = series;

    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      priceLineRef.current = null;
    };
  }, []);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    const data = candles
      .filter((c) => Number.isFinite(c.open))
      .map((c) => ({
        time: Math.floor(Date.parse(c.start) / 1000) as UTCTimestamp,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      }));
    series.setData(data);
    chartRef.current?.timeScale().fitContent();
  }, [candles]);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series || livePrice === null) return;
    if (priceLineRef.current) {
      priceLineRef.current.applyOptions({ price: livePrice });
    } else {
      priceLineRef.current = series.createPriceLine({
        price: livePrice,
        color: "#eab308",
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: "live",
      });
    }
  }, [livePrice]);

  return <div ref={containerRef} className="w-full" style={{ height }} />;
}
