import { useState } from "react";
import { fetchCapacity, fetchPortCapacity, type CapacityPort, type Forecast, type PortCapacity } from "./api";
import { href } from "./nav";
import { TrafficChart } from "./TrafficChart";
import { axisBps, formatBps, niceScale } from "./units";
import { useFetch } from "./useFetch";

// Capacity: each port's and trunk's busiest hour (95th percentile) as a share
// of the speed the switch reports, and when its trend over 4 weeks of daily 95th percentiles reaches 70 %.

const WINDOWS: [number, string][] = [[1, "24 h"], [7, "7 days"], [30, "30 days"]];
const WEEKS = [10080, 40320];

/** What the forecast says, in a few words. */
export function forecastText(f: Forecast, threshold: number): string {
  const pct = `${Math.round(threshold * 100)} %`;
  switch (f.status) {
    case "over threshold": return `over ${pct} now`;
    case "not rising": return "not rising";
    case "no speed": return "speed not known";
    case "not enough data": return `needs 7 days of data (has ${f.days_of_data})`;
    default: {
      const range = f.earliest === f.latest ? "" : ` (${f.earliest}–${f.latest ?? "never"})`;
      return `${pct} in about ${f.days} day${f.days === 1 ? "" : "s"}${range}`;
    }
  }
}

const percent = (v: number | null) => (v === null ? "—" : `${v < 10 ? v.toFixed(1) : Math.round(v)} %`);

function LoadBar({ value, threshold }: { value: number | null; threshold: number }) {
  return (
    <div className="load">
      <span className={`track cap${value !== null && value >= threshold * 100 ? " over" : ""}`}
            style={{ ["--mark" as string]: `${threshold * 100}%` }}>
        <i style={{ width: `${Math.min(100, value ?? 0)}%` }} />
      </span>
      <span className="num">{percent(value)}</span>
    </div>
  );
}

const W = 640, H = 180, PAD = { top: 14, right: 70, bottom: 24, left: 64 };

/** Daily 95th percentiles of the last 4 weeks, their trend line and the 70 % line. */
function ForecastChart({ port }: { port: PortCapacity }) {
  const f = port.forecast;
  const today = new Date(new Date().toISOString().slice(0, 10)).getTime() / 86400_000;
  const points = port.daily.map((d) => ({ x: new Date(d.day).getTime() / 86400_000 - today, y: d.p95_bps }))
    .filter((p) => p.x > -28);
  if (points.length === 0) return null;
  const ahead = f.status === "rising" && f.days !== null ? Math.min(f.days, 180) : 0;
  const x0 = -27, x1 = Math.max(1, ahead);
  const trend = f.today_bps !== null && f.slope_bps_per_day !== null
    ? (x: number) => f.today_bps! + f.slope_bps_per_day! * x : null;
  const { top, step } = niceScale(Math.max(f.threshold_bps ?? 0, ...points.map((p) => p.y), trend ? trend(x1) : 0) * 1.05);
  const x = (v: number) => PAD.left + ((v - x0) / (x1 - x0)) * (W - PAD.left - PAD.right);
  const y = (v: number) => H - PAD.bottom - (Math.max(0, v) / top) * (H - PAD.top - PAD.bottom);
  const ticks = Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step);
  const day = (offset: number) => new Date((today + offset) * 86400_000).toLocaleDateString([], { month: "short", day: "numeric" });

  return (
    <div className="chart">
      <div className="chart-head">
        <strong>Busiest hour per day, and its trend</strong>
        <span className="legend-inline">
          <span><i className="key in" />daily 95th percentile</span>
          <span><i className="key trend" />trend</span>
          <span><i className="key limit" />{Math.round(port.threshold * 100)} % of {formatBps(port.speed_bps)}</span>
        </span>
      </div>
      <div className="chart-plot">
        <svg viewBox={`0 0 ${W} ${H}`} role="img"
             aria-label={`${port.interface}: daily 95th percentiles over 4 weeks; ${forecastText(f, port.threshold)}`}>
          {ticks.map((v) => (
            <g key={v}>
              <line className="grid" x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} />
              <text className="axis" x={PAD.left - 8} y={y(v) + 3} textAnchor="end">{axisBps(v, top)}</text>
            </g>
          ))}
          {[x0, 0, x1].filter((v, i, all) => all.indexOf(v) === i).map((v, i) => (
            <text key={v} className="axis" x={x(v)} y={H - 6} textAnchor={i === 0 ? "start" : v === x1 ? "end" : "middle"}>
              {v === 0 ? "today" : day(v)}
            </text>
          ))}
          {f.threshold_bps !== null && (
            <>
              <line className="limit" x1={PAD.left} x2={W - PAD.right} y1={y(f.threshold_bps)} y2={y(f.threshold_bps)} />
              <text className="end-label" x={W - PAD.right + 6} y={y(f.threshold_bps) + 3}>{Math.round(port.threshold * 100)} %</text>
            </>
          )}
          {trend && <line className="trend" x1={x(x0)} x2={x(x1)} y1={y(trend(x0))} y2={y(trend(x1))} />}
          {points.map((p) => (
            <circle key={p.x} className="dot in" cx={x(p.x)} cy={y(p.y)} r={4}>
              <title>{`${day(p.x)}: ${formatBps(p.y)}`}</title>
            </circle>
          ))}
        </svg>
      </div>
      <p className="faint small">{forecastText(f, port.threshold)}. A straight line through the last 4 weeks; the range comes from how
        widely the days scatter around it.</p>
    </div>
  );
}

function PortDetail({ port }: { port: CapacityPort }) {
  const data = useFetch(() => fetchPortCapacity(port.device, port.interface, 28), [port.device, port.interface]);
  return (
    <section className="panel" data-tour="capacity-port">
      <header>
        <h2><a className="mono" href={href.switchPage(port.device)}>{port.device}</a> <span className="mono">{port.interface}</span></h2>
        <span className="hint">{formatBps(port.speed_bps)}</span>
      </header>
      {data.error && <p className="error">{data.error}</p>}
      {data.data && <ForecastChart port={data.data} />}
      <TrafficChart device={port.device} iface={port.interface} ranges={WEEKS} initial={40320} />
    </section>
  );
}

export function CapacityPage({ site }: { site: string }) {
  const [days, setDays] = useState(7);
  const [picked, setPicked] = useState<string | null>(null);
  const data = useFetch(() => (site ? fetchCapacity(site, days) : Promise.resolve(null)), [site, days]);
  if (!site) return <p className="empty muted">Pick a fabric (top right): capacity is shown per fabric.</p>;
  const ports = data.data?.ports ?? [];
  const threshold = data.data?.threshold ?? 0.7;
  const key = (p: CapacityPort) => `${p.device} ${p.interface}`;
  const chosen = ports.find((p) => key(p) === picked);

  return (
    <div className="capacity-page">
      <section className="panel" data-tour="capacity-ports">
        <header>
          <h2>Busiest ports</h2>
          <span className="hint">busiest hour (95th percentile) as a share of the port's speed · click a row for its weeks</span>
          <span className="toggle" role="group" aria-label="Window">
            {WINDOWS.map(([d, label]) => (
              <button key={d} aria-pressed={days === d} onClick={() => setDays(d)}>{label}</button>
            ))}
          </span>
        </header>
        {data.error && <p className="error">{data.error}</p>}
        {data.data && ports.length === 0 && <p className="empty muted">No traffic stored yet: the first hour is written two minutes after it ends.</p>}
        {ports.length > 0 && (
          <div className="tablewrap">
            <table className="devices capacity">
              <thead>
                <tr><th>Switch</th><th>Port</th><th>Speed</th><th>Busiest hour in / out</th><th>Average in / out</th>
                  <th>Load</th><th>Reaches {Math.round(threshold * 100)} %</th></tr>
              </thead>
              <tbody>
                {ports.map((p) => (
                  <tr key={key(p)} className={`clickable${picked === key(p) ? " selected" : ""}`} tabIndex={0}
                      onClick={() => setPicked(key(p))} onKeyDown={(e) => e.key === "Enter" && setPicked(key(p))}>
                    <td className="mono">{p.device}</td>
                    <td className="mono">{p.interface}</td>
                    <td className="num">{formatBps(p.speed_bps)}</td>
                    <td className="num">{formatBps(p.in_p95_bps)} / {formatBps(p.out_p95_bps)}</td>
                    <td className="num">{formatBps(p.in_avg_bps)} / {formatBps(p.out_avg_bps)}</td>
                    <td><LoadBar value={p.p95_percent} threshold={threshold} /></td>
                    <td className={p.forecast.status === "rising" || p.forecast.status === "over threshold" ? "" : "faint"}>
                      {forecastText(p.forecast, threshold)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {chosen && <PortDetail key={key(chosen)} port={chosen} />}
    </div>
  );
}
