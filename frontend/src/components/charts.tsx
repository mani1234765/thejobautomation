"use client";

import { Table2, TrendingUp } from "lucide-react";
import { useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis } from "recharts";
import { cn } from "@/lib/format";
import { Button } from "./ui/button";
import { Tooltip } from "./ui/overlay";

export type Series = { key: string; label: string; color: string };

/** Fixed categorical order — colour follows the series, never its rank (validated palette). */
export const SERIES_COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)"] as const;

function ChartTooltip({ active, payload, label, series, labelFormat }: {
  active?: boolean; payload?: { dataKey: string; value: number }[]; label?: string; series: Series[];
  labelFormat: (l: string) => string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border border-border bg-surface px-2.5 py-2 text-xs shadow-md">
      <p className="mb-1 font-medium text-fg">{label ? labelFormat(label) : ""}</p>
      {series.map((s) => {
        const v = payload.find((p) => p.dataKey === s.key)?.value ?? 0;
        return (
          <p key={s.key} className="flex items-center gap-2 text-fg-muted">
            <span className="size-2 rounded-full" style={{ background: s.color }} />
            <span className="flex-1">{s.label}</span>
            <span className="tabular font-medium text-fg">{v}</span>
          </p>
        );
      })}
    </div>
  );
}

/** Multi-series line chart with legend, crosshair tooltip and a table view (accessibility relief). */
export function TrendChart({
  data, xKey, series, height = 200, labelFormat = (l) => l, tickFormat = (l) => l, empty,
}: {
  data: Record<string, number | string>[]; xKey: string; series: Series[]; height?: number;
  labelFormat?: (l: string) => string; tickFormat?: (l: string) => string; empty?: React.ReactNode;
}) {
  const [table, setTable] = useState(false);
  const hasData = data.some((d) => series.some((s) => Number(d[s.key]) > 0));
  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-2">
        <ul className="flex flex-wrap gap-x-4 gap-y-1" aria-label="Legend">
          {series.map((s) => (
            <li key={s.key} className="flex items-center gap-1.5 text-xs text-fg-muted">
              <span className="h-0.5 w-3 rounded-full" style={{ background: s.color }} aria-hidden />
              {s.label}
              <span className="tabular font-medium text-fg">
                {data.reduce((a, d) => a + Number(d[s.key] ?? 0), 0).toLocaleString("en-IN")}
              </span>
            </li>
          ))}
        </ul>
        <Tooltip content={table ? "Show chart" : "Show as table"}>
          <Button variant="ghost" size="icon-sm" aria-pressed={table} aria-label="Toggle table view" onClick={() => setTable((t) => !t)}>
            {table ? <TrendingUp /> : <Table2 />}
          </Button>
        </Tooltip>
      </div>
      {!hasData && empty ? (
        <div style={{ height }} className="flex items-center justify-center">{empty}</div>
      ) : table ? (
        <div className="overflow-auto rounded-md border border-border" style={{ maxHeight: height }}>
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-surface-2 text-fg-muted">
              <tr>
                <th className="px-2.5 py-1.5 text-left font-medium">Period</th>
                {series.map((s) => <th key={s.key} className="px-2.5 py-1.5 text-right font-medium">{s.label}</th>)}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {data.map((d) => (
                <tr key={String(d[xKey])}>
                  <td className="px-2.5 py-1 text-fg-muted">{labelFormat(String(d[xKey]))}</td>
                  {series.map((s) => <td key={s.key} className="tabular px-2.5 py-1 text-right">{d[s.key]}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div style={{ height }} role="img" aria-label={`Line chart of ${series.map((s) => s.label).join(", ")}`}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
              <XAxis dataKey={xKey} tickLine={false} axisLine={false} tick={{ fontSize: 11, fill: "var(--fg-subtle)" }}
                tickFormatter={tickFormat} minTickGap={28} />
              <YAxis allowDecimals={false} tickLine={false} axisLine={false} tick={{ fontSize: 11, fill: "var(--fg-subtle)" }} width={36} />
              <RTooltip cursor={{ stroke: "var(--border-strong)", strokeWidth: 1 }}
                content={(props) => <ChartTooltip {...(props as object)} series={series} labelFormat={labelFormat} />} />
              {series.map((s) => (
                <Line key={s.key} type="monotone" dataKey={s.key} stroke={s.color} strokeWidth={2} dot={false}
                  activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface)" }} isAnimationActive={false} />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

/** Horizontal bar list (HTML) for ranked magnitudes — readable labels, hover detail, no legend needed. */
export function BarList({
  items, max, format = (v) => v.toLocaleString("en-IN"), className, highlight,
}: {
  items: { label: string; value: number; hint?: string; muted?: boolean; href?: string }[]; max?: number;
  format?: (v: number) => string; className?: string; highlight?: (label: string) => boolean;
}) {
  const top = max ?? Math.max(1, ...items.map((i) => i.value));
  return (
    <ul className={cn("flex flex-col gap-1", className)}>
      {items.map((i) => (
        <li key={i.label}>
          <Tooltip content={i.hint}>
            <div className="group relative flex h-7 items-center gap-3 rounded-md px-2 hover:bg-surface-2">
              <div className="absolute inset-y-1 left-0 rounded-[4px] bg-surface-3 transition-colors group-hover:bg-border"
                style={{ width: `${Math.max(2, (i.value / top) * 100)}%` }} aria-hidden>
                {highlight?.(i.label) && <div className="absolute inset-y-0 left-0 w-[3px] rounded-l-[4px] bg-accent" />}
              </div>
              <span className={cn("relative flex-1 truncate text-[13px]", i.muted ? "text-fg-muted" : "text-fg")}>{i.label}</span>
              <span className="relative tabular text-xs font-medium text-fg-muted">{format(i.value)}</span>
            </div>
          </Tooltip>
        </li>
      ))}
    </ul>
  );
}
