import { useEffect, useRef, useState, type PointerEvent } from "react";
import { fetchHistory, fetchPortCapacity, type HistoryPoint } from "./api";
import { axisBps, formatBps, niceScale } from "./units";
import { useFetch } from "./useFetch";

// One port's traffic over time: In and Out on one axis in bits/s. Colours are a
// validated pair (dataviz validator): light #00756e / #b0560f, dark #52d3c5 /
// #f0a54a (--series-in / --series-out). Legend and end labels, so identity is never colour alone.

// Up to 24 h from Prometheus; 7 days and 4 weeks from the hourly rollups (hourly averages).
export const RANGES = [15, 60, 360, 1440, 10080, 40320];
const RANGE_LABEL: Record<number, string> = { 15: "15 min", 60: "1 h", 360: "6 h", 1440: "24 h", 10080: "7 d", 40320: "4 w" };
const DAY = 1440;
const W = 640, H = 200, PAD = { top: 12, right: 62, bottom: 24, left: 64 };

const timeLabel = (t: number, minutes: number) => minutes > DAY
  ? new Date(t * 1000).toLocaleDateString([], { month: "short", day: "numeric" })
  : new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

/** Up to 24 h: Prometheus' points; longer: the stored hourly averages. */
function load(device: string, iface: string, minutes: number): Promise<{ step: number; points: HistoryPoint[] }> {
  if (minutes <= DAY) return fetchHistory(device, iface, minutes);
  return fetchPortCapacity(device, iface, minutes / DAY).then((d) => ({
    step: 3600, points: d.hours.map((h) => ({ t: h.t, in_bps: h.in_avg_bps ?? null, out_bps: h.out_avg_bps ?? null })),
  }));
}

/** Path through the points, broken where a value is missing. */
function path(points: HistoryPoint[], key: "in_bps" | "out_bps", x: (t: number) => number, y: (v: number) => number): string {
  let d = "";
  let pen = false;
  for (const p of points) {
    const v = p[key];
    if (v === null || v === undefined) {
      pen = false;
      continue;
    }
    d += `${pen ? "L" : "M"}${x(p.t).toFixed(1)} ${y(v).toFixed(1)}`;
    pen = true;
  }
  return d;
}

export function TrafficChart({ device, iface, ranges = RANGES, initial = 60 }:
  { device: string; iface: string; ranges?: number[]; initial?: number }) {
  const [minutes, setMinutes] = useState(initial);
  const [table, setTable] = useState(false);
  const { data, error, reload } = useFetch(() => load(device, iface, minutes), [device, iface, minutes]);
  const [hover, setHover] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    const id = window.setInterval(reload, 30_000);
    return () => window.clearInterval(id);
  }, [reload]);

  const points = data?.points ?? [];
  const values = points.flatMap((p) => [p.in_bps ?? 0, p.out_bps ?? 0]);
  const { top, step } = niceScale(Math.max(0, ...values));
  const t0 = points[0]?.t ?? 0, t1 = points[points.length - 1]?.t ?? 1;
  const x = (t: number) => PAD.left + ((t - t0) / Math.max(1, t1 - t0)) * (W - PAD.left - PAD.right);
  const y = (v: number) => H - PAD.bottom - (v / top) * (H - PAD.top - PAD.bottom);
  const ticks = Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step);
  const xTicks = points.length > 1 ? [0, 0.25, 0.5, 0.75, 1].map((f) => t0 + f * (t1 - t0)) : [];
  const last = [...points].reverse();
  const lastIn = last.find((p) => p.in_bps !== null);
  const lastOut = last.find((p) => p.out_bps !== null);
  const point = hover !== null ? points[hover] : null;

  const move = (e: PointerEvent<SVGRectElement>) => {
    const box = svgRef.current?.getBoundingClientRect();
    if (!box || points.length === 0) return;
    const sx = ((e.clientX - box.left) / box.width) * W;
    let best = 0;
    for (let i = 1; i < points.length; i++) if (Math.abs(x(points[i].t) - sx) < Math.abs(x(points[best].t) - sx)) best = i;
    setHover(best);
  };

  // End labels: in front of the line end, nudged apart when the two ends are close.
  let inY = lastIn ? y(lastIn.in_bps ?? 0) : 0;
  let outY = lastOut ? y(lastOut.out_bps ?? 0) : 0;
  if (lastIn && lastOut && Math.abs(inY - outY) < 12) {
    const mid = (inY + outY) / 2;
    [inY, outY] = inY <= outY ? [mid - 6, mid + 6] : [mid + 6, mid - 6];
  }

  return (
    <div className="chart">
      <div className="chart-head">
        <strong className="mono">{iface}</strong>
        <span className="legend-inline">
          <span><i className="key in" />In</span>
          <span><i className="key out" />Out</span>
        </span>
        <span className="toggle" role="group" aria-label="Time range">
          {ranges.map((m) => (
            <button key={m} aria-pressed={minutes === m} onClick={() => setMinutes(m)}>
              {RANGE_LABEL[m]}
            </button>
          ))}
        </span>
        <button className="linklike small" onClick={() => setTable(!table)}>
          {table ? "Chart" : "Table"}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {data && points.length === 0 && <p className="muted">No traffic recorded for this port in the last {RANGE_LABEL[minutes]}.</p>}
      {points.length > 0 && !table && (
        <div className="chart-plot">
          <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${iface} traffic, last ${RANGE_LABEL[minutes]}: in and out in bits per second`}>
            {ticks.map((v) => (
              <g key={v}>
                <line className="grid" x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} />
                <text className="axis" x={PAD.left - 8} y={y(v) + 3} textAnchor="end">
                  {axisBps(v, top)}
                </text>
              </g>
            ))}
            {xTicks.map((t, i) => (
              <text key={i} className="axis" x={x(t)} y={H - 6} textAnchor={i === 0 ? "start" : i === xTicks.length - 1 ? "end" : "middle"}>
                {timeLabel(t, minutes)}
              </text>
            ))}
            <path className="series in" d={path(points, "in_bps", x, y)} />
            <path className="series out" d={path(points, "out_bps", x, y)} />
            {lastIn && <text className="end-label" x={x(lastIn.t) + 6} y={inY + 3}>In</text>}
            {lastOut && <text className="end-label" x={x(lastOut.t) + 6} y={outY + 3}>Out</text>}
            {point && (
              <g className="crosshair">
                <line x1={x(point.t)} x2={x(point.t)} y1={PAD.top} y2={H - PAD.bottom} />
                {point.in_bps !== null && <circle className="dot in" cx={x(point.t)} cy={y(point.in_bps)} r={4} />}
                {point.out_bps !== null && <circle className="dot out" cx={x(point.t)} cy={y(point.out_bps)} r={4} />}
              </g>
            )}
            <rect className="hit" x={PAD.left} y={PAD.top} width={W - PAD.left - PAD.right} height={H - PAD.top - PAD.bottom}
              onPointerMove={move} onPointerLeave={() => setHover(null)} />
          </svg>
          {point && (
            <div className="readout" style={{ left: `${Math.min(86, Math.max(14, (x(point.t) / W) * 100))}%` }}>
              <b>{minutes > DAY ? new Date(point.t * 1000).toLocaleString() : new Date(point.t * 1000).toLocaleTimeString()}</b>
              <span><i className="key in" />In {formatRate(point.in_bps)}</span>
              <span><i className="key out" />Out {formatRate(point.out_bps)}</span>
            </div>
          )}
        </div>
      )}
      {points.length > 0 && table && (
        <div className="tablewrap chart-table">
          <table className="devices">
            <thead>
              <tr><th>Time</th><th>In</th><th>Out</th></tr>
            </thead>
            <tbody>
              {[...points].reverse().map((p) => (
                <tr key={p.t}>
                  <td className="num">{minutes > DAY ? new Date(p.t * 1000).toLocaleString() : new Date(p.t * 1000).toLocaleTimeString()}</td>
                  <td className="num">{formatRate(p.in_bps)}</td>
                  <td className="num">{formatRate(p.out_bps)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data && <p className="faint small">{minutes > DAY ? "One point per hour, its average (kept 13 months)."
        : `One point per ${data.step} s, each the average over its interval.`}</p>}
    </div>
  );
}

const formatRate = (v: number | null) => (v === null ? "—" : formatBps(v));
