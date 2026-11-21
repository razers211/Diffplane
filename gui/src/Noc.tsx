import { useEffect, useRef, useState, type PointerEvent } from "react";
import { fetchEvents, fetchFabricLogs, fetchFabrics, fetchFlows, fetchHealth, fetchOverview, fetchTopology, fetchTraffic } from "./api";
import { ScoreGauge } from "./Health";
import { href } from "./nav";
import { alertLabel, axisBps, edgeIn, keep, nextFabric, niceMax, ROTATE_S, talkers, TRAFFIC_WINDOW_S, type Sample } from "./noc";
import { load } from "./overview";
import { eventTitle } from "./Timeline";
import { formatBps } from "./units";
import { useFetch } from "./useFetch";

// The NOC dashboard: health, firing alerts, live traffic, busiest ports, top
// talkers, switch logs and the timeline of one fabric. Wall mode (#/overview/noc/wall) is the same page
// full screen with larger type, rotating through the fabrics, no click needed.

const TRAFFIC_MS = 5_000;   // the collector's counter interval
const SLOW_MS = 30_000;
const LOGS_MS = 10_000;
const TONE = { problem: "bad", recovered: "good", change: "info" } as const;

const clock = () => new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const ago = (unix: number | null) => {
  if (unix === null) return "";
  const m = Math.max(0, Math.round((Date.now() / 1000 - unix) / 60));
  return m < 60 ? `${m} min` : m < 2880 ? `${Math.round(m / 60)} h` : `${Math.round(m / 1440)} d`;
};
const time = (iso: string) => new Date(iso.includes("T") ? iso : `${iso.replace(" ", "T")}Z`)
  .toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

function every(ms: number, reload: () => void) {
  useEffect(() => {
    const id = window.setInterval(reload, ms);
    return () => window.clearInterval(id);
  }, [ms, reload]);
}

export function Noc({ site, wall }: { site: string; wall: boolean }) {
  const fabrics = useFetch(fetchFabrics, []);
  const sites = (fabrics.data ?? []).map((f) => f.site);
  const [shown, setShown] = useState(site);
  useEffect(() => setShown(site), [site]);
  const fabric = shown && sites.includes(shown) ? shown : sites[0] ?? "";

  // Wall mode: the next fabric every ROTATE_S seconds, the screen kept awake.
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!wall) return;
    setElapsed(0);
    const id = window.setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => window.clearInterval(id);
  }, [wall, fabric]);
  useEffect(() => {
    if (wall && elapsed >= ROTATE_S && sites.length > 1) setShown(nextFabric(sites, fabric));
  }, [wall, elapsed, fabric, sites.join(" ")]);
  useEffect(() => {
    if (!wall) return;
    let lock: { release: () => Promise<void> } | null = null;
    const nav = navigator as Navigator & { wakeLock?: { request: (t: "screen") => Promise<{ release: () => Promise<void> }> } };
    nav.wakeLock?.request("screen").then((l) => { lock = l; }).catch(() => {});
    return () => { lock?.release().catch(() => {}); };
  }, [wall]);

  const [now, setNow] = useState(clock);
  useEffect(() => {
    const id = window.setInterval(() => setNow(clock()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const enterWall = () => {
    document.documentElement.requestFullscreen?.().catch(() => {});   // refused in some frames: the page still works
    window.location.hash = "#/overview/noc/wall";
  };
  const leaveWall = () => {
    if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
    window.location.hash = "#/overview/noc";
  };

  if (fabrics.data && sites.length === 0) return <p className="empty">No fabric yet.</p>;
  return (
    <div className={`noc-page${wall ? " wall" : ""}`}>
      <div className="noc-tools">
        <div className="toggle" role="group" aria-label="Fabric">
          {sites.map((s) => <button key={s} aria-pressed={s === fabric} onClick={() => setShown(s)}>{s}</button>)}
        </div>
        {wall ? <button className="button" onClick={leaveWall}>Leave wall mode</button>
          : <button className="button" onClick={enterWall} data-tour="noc-wall">Wall mode</button>}
        {wall && sites.length > 1 && (
          <span className="rotbar" aria-label={`next fabric in ${Math.max(0, ROTATE_S - elapsed)} s`}>
            <i style={{ width: `${Math.min(100, (elapsed / ROTATE_S) * 100)}%` }} />
          </span>
        )}
        <span className="clock num">{now}</span>
      </div>
      {fabric && <Board key={fabric} site={fabric} />}
    </div>
  );
}

function Board({ site }: { site: string }) {
  const health = useFetch(() => fetchHealth(site), [site]);
  const overview = useFetch(() => fetchOverview(site), [site]);
  const topology = useFetch(() => fetchTopology(site), [site]);
  const flows = useFetch(() => fetchFlows(site, 5, "edge").catch(() => null), [site]);
  const events = useFetch(() => fetchEvents({ site, limit: 8 }), [site]);
  const logs = useFetch(() => fetchFabricLogs(site, { severity: 4, hours: 24, limit: 40 }), [site]);
  every(SLOW_MS, health.reload);
  every(SLOW_MS, overview.reload);
  every(SLOW_MS, topology.reload);
  every(SLOW_MS, flows.reload);
  every(SLOW_MS, events.reload);
  every(LOGS_MS, logs.reload);

  // Live traffic: sampled here every 5 s (nothing stored), so the chart fills over two minutes.
  const [samples, setSamples] = useState<Sample[]>([]);
  const topologyRef = useRef(topology.data);
  topologyRef.current = topology.data;
  useEffect(() => {
    let live = true;
    const sample = () => fetchTraffic(site).then((t) => {
      if (live && t.telemetry.ok) setSamples((s) => keep(s, { t: Date.now(), bps: edgeIn(t.interfaces, topologyRef.current ?? null) }));
    }).catch(() => {});
    sample();
    const id = window.setInterval(sample, TRAFFIC_MS);
    return () => { live = false; window.clearInterval(id); };
  }, [site]);

  const [errorsOnly, setErrorsOnly] = useState(false);
  const h = health.data;
  const alerts = overview.data?.alerts ?? [];
  const ports = (overview.data?.ports ?? []).slice(0, 6);
  const talk = talkers(flows.data ?? null);
  const lines = (logs.data ?? []).filter((l) => !errorsOnly || l.severity <= 3).slice(0, 14);

  return (
    <div className="noc">
      <section className="panel c4" aria-label="Health" data-tour="noc-health">
        <header><h2>Health</h2><a className="hint" href="#/overview/health">worst switch or link counts</a></header>
        {h ? (
          <div className="noc-health">
            <ScoreGauge score={h.score} level={h.level} size={130} />
            {h.reasons.length === 0 ? <p className="muted">Nothing wrong.</p> : (
              <ul className="reasons">
                {h.reasons.slice(0, 4).map((r, i) => <li key={i}><span className="num points">{r.points}</span> {r.text}</li>)}
                {h.others_below_good > 0 && <li className="faint">and {h.others_below_good} more below 90</li>}
              </ul>
            )}
          </div>
        ) : <p className="empty">{health.error ?? "Loading…"}</p>}
      </section>

      <section className="panel c8" aria-label="Firing alerts" data-tour="noc-alerts">
        <header><h2>Firing alerts</h2><span className="hint">{alerts.length} firing</span></header>
        {alerts.length === 0 ? <p className="empty">{overview.data?.telemetry.ok === false ? "Telemetry unavailable." : "No alert is firing."}</p> : (
          <ul className="noc-alerts">
            {alerts.map((a, i) => {
              const tone = a.severity === "critical" ? "bad" : "warn";
              return (
                <li key={i} className={tone}>
                  <span className="ico" aria-hidden="true">{tone === "bad" ? "■" : "▲"}</span>
                  <div>
                    <strong>{tone === "bad" ? "Critical: " : "Warning: "}{alertLabel(a.alert)}</strong>
                    {a.device && <a className="mono sub" href={href.switchPage(a.device)}>{a.device}</a>}
                  </div>
                  <span className="faint num">{ago(a.since)}</span>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section className="panel c8" aria-label="Fabric traffic" data-tour="noc-traffic">
        <header><h2>Fabric traffic</h2><span className="hint">entering at edge ports, last 2 minutes, every 5 s</span></header>
        <TrafficLine samples={samples} />
      </section>

      <section className="panel c4" aria-label="Busiest ports">
        <header><h2>Busiest ports</h2><span className="hint">share of speed · mark at 70 %</span></header>
        {ports.length === 0 ? <p className="empty">No traffic data yet.</p> : (
          <div className="noc-bars">
            {ports.map((p) => {
              const pct = load(p);
              return (
                <div key={`${p.device} ${p.interface}`} className="b">
                  <span className="lbl"><b>{p.device}</b> <span className="mono">{p.interface}</span></span>
                  <span className="val num">{pct === null ? formatBps(p.bps) : `${pct < 1 ? "<1" : Math.round(pct)} %`}</span>
                  <span className="track"><i className={pct !== null && pct >= 70 ? "hot" : ""} style={{ width: `${pct ?? 0}%` }} /><span className="cap" /></span>
                </div>
              );
            })}
          </div>
        )}
      </section>

      <section className="panel c5" aria-label="Top talkers">
        <header><h2>Top talkers</h2><a className="hint" href="#/overview/flows">NetStream, last 5 min</a></header>
        {talk.length === 0 ? <p className="empty">{flows.data === null ? "Flows unavailable." : "No flow records in the last 5 minutes (NetStream may be off)."}</p> : (
          <div className="noc-bars">
            {talk.map((t, i) => (
              <div key={i} className="b">
                <span className="lbl">{t.from} <span aria-label="to">→</span> {t.to}</span>
                <span className="val num">{formatBps(t.bps)}</span>
                <span className="track"><i style={{ width: `${(t.bps / talk[0].bps) * 100}%` }} /></span>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="panel c7" aria-label="Switch logs" data-tour="noc-logs">
        <header>
          <h2>Switch logs</h2>
          <div className="toggle" role="group" aria-label="Severity">
            <button aria-pressed={!errorsOnly} onClick={() => setErrorsOnly(false)}>Warnings and worse</button>
            <button aria-pressed={errorsOnly} onClick={() => setErrorsOnly(true)}>Errors</button>
          </div>
        </header>
        {logs.error ? <p className="empty">{logs.error}</p> : lines.length === 0 ? <p className="empty">Nothing at {errorsOnly ? "error" : "warning"} or worse in the last 24 hours.</p> : (
          <div className="noc-logs">
            {lines.map((l, i) => (
              <div key={i}>
                <span className="faint num">{time(l.time)}</span>
                <a className="mono" href={href.switchPage(l.device ?? "", "logs")}>{l.device}</a>
                <span className={`sev ${l.severity <= 3 ? "e" : "w"}`} title={l.severity_name}>{l.severity <= 3 ? "ERR" : "WRN"}</span>
                <span className="msg" title={`${l.tag} ${l.message}`}><b>{l.tag.split("/")[0]}</b> {l.message}</span>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="panel c12" aria-label="Timeline">
        <header><h2>Timeline</h2><a className="hint" href="#/overview/timeline">everything that changed</a></header>
        {(events.data ?? []).length === 0 ? <p className="empty">Nothing changed lately.</p> : (
          <ul className="timeline">
            {(events.data ?? []).map((e, i) => (
              <li key={i} className={`ev-${e.severity}`}>
                <span className={`dot ${TONE[e.severity]}`} />
                <div><strong>{eventTitle(e)}</strong>{e.device && <> on <span className="mono">{e.device}</span></>}</div>
                <time>{time(e.at)}</time>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function TrafficLine({ samples }: { samples: Sample[] }) {
  const [hover, setHover] = useState<number | null>(null);
  // Drawn at the box's own width, so its text keeps its size from a phone to a wall screen.
  const box = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(600);
  useEffect(() => {
    if (!box.current) return;
    const seen = new ResizeObserver(([e]) => setW(Math.max(240, Math.round(e.contentRect.width))));
    seen.observe(box.current);
    return () => seen.disconnect();
  }, [samples.length > 0]);
  const last = samples[samples.length - 1];
  if (!last) return <p className="empty">Waiting for the first sample.</p>;
  const H = 170, l = 36, r = 8, t = 10, b = 20;
  const max = niceMax(Math.max(...samples.map((s) => s.bps)) * 1.15);
  const x = (ms: number) => l + ((ms - (last.t - TRAFFIC_WINDOW_S * 1000)) / (TRAFFIC_WINDOW_S * 1000)) * (W - l - r);
  const y = (v: number) => H - b - (v / max) * (H - t - b);
  const pts = samples.map((s) => `${x(s.t).toFixed(1)},${y(s.bps).toFixed(1)}`).join(" ");
  const peak = Math.max(...samples.map((s) => s.bps));
  const shown = hover === null ? null : samples[hover];
  const pick = (e: PointerEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * W;
    let best = 0;
    samples.forEach((s, i) => { if (Math.abs(x(s.t) - px) < Math.abs(x(samples[best].t) - px)) best = i; });
    setHover(best);
  };
  return (
    <div className="noc-traffic">
      <div className="big"><span className="num">{formatBps(last.bps)}</span><span className="faint">peak {formatBps(peak)}</span></div>
      <div className="chartbox" ref={box}>
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Traffic entering the fabric, last two minutes, now ${formatBps(last.bps)}`}
          onPointerMove={pick} onPointerLeave={() => setHover(null)}>
          {[0, 0.5, 1].map((f) => (
            <g key={f}>
              <line className="grid" x1={l} x2={W - r} y1={y(f * max)} y2={y(f * max)} />
              <text className="axis" x={l - 6} y={y(f * max) + 3} textAnchor="end">{axisBps(f * max)}</text>
            </g>
          ))}
          <text className="axis" x={l} y={H - 4}>−2 min</text>
          <text className="axis" x={W - r} y={H - 4} textAnchor="end">now</text>
          {samples.length > 1 && <polygon className="area" points={`${x(samples[0].t)},${H - b} ${pts} ${x(last.t)},${H - b}`} />}
          <polyline className="line" points={pts} />
          <circle className="end" cx={x(last.t)} cy={y(last.bps)} r="4" />
          {shown && <line className="cross" x1={x(shown.t)} x2={x(shown.t)} y1={t} y2={H - b} />}
        </svg>
        {shown && (
          <div className="tip" style={{ left: `${Math.min(80, (x(shown.t) / W) * 100)}%` }}>
            <b>{formatBps(shown.bps)}</b><br /><span className="faint">{Math.round((last.t - shown.t) / 1000)} s ago</span>
          </div>
        )}
      </div>
    </div>
  );
}
