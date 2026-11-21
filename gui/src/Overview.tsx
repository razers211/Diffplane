import { Counter } from "./Counter";
import { TopologyView } from "./TopologyView";
import { useEffect } from "react";
import { fetchDeployJobs, fetchDevices, fetchDrift, fetchHealth, fetchOverview, fetchTopology } from "./api";
import { ICONS } from "./icons";
import { deployTone, load, summarize, type Tile } from "./overview";
import { formatBps } from "./units";
import { useFetch } from "./useFetch";

// The fabric overview (the GUI opens here). Refreshed every 30 s, like the Drift page.

const POLL_MS = 30_000;
const ROLLOUT_MS = 3_000;

const TILE_ICONS: Record<Tile["key"], keyof typeof ICONS> = {
  switches: "devices",
  sync: "drift",
  collector: "topology",
  alerts: "alerts",
  approvals: "deploys",
};

function when(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toDateString() === new Date().toDateString() ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : d.toLocaleDateString();
}

function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return <span className="faint">—</span>;
  const w = 120, h = 26, max = Math.max(...values, 1);
  const pts = values.map((v, i) => [2 + (i * (w - 4)) / (values.length - 1), h - 3 - (v / max) * (h - 7)]);
  const line = pts.map((p) => p.map((n) => n.toFixed(1)).join(",")).join(" ");
  const [ex, ey] = pts[pts.length - 1];
  return (
    <svg className="spark" viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
      <polygon className="area" points={`2,${h - 1} ${line} ${w - 2},${h - 1}`} />
      <polyline className="line" points={line} />
      <circle className="end" cx={ex} cy={ey} r="2.2" />
    </svg>
  );
}

export function Overview({ site }: { site: string }) {
  const devices = useFetch(() => fetchDevices(site || undefined), [site]);
  const drift = useFetch(fetchDrift, []);
  const topology = useFetch(() => fetchTopology(site || undefined), [site]);
  const deploys = useFetch(fetchDeployJobs, []);
  const overview = useFetch(() => fetchOverview(site || undefined), [site]);
  const health = useFetch(() => (site ? fetchHealth(site).catch(() => null) : Promise.resolve(null)), [site]);

  useEffect(() => {
    const id = window.setInterval(() => {
      drift.reload();
      topology.reload();
      deploys.reload();
      overview.reload();
      health.reload();
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [drift.reload, topology.reload, deploys.reload, overview.reload, health.reload]);

  // A deploy prepared or rolling out is shown on the map, refreshed every few seconds meanwhile.
  const rollout = (deploys.data ?? []).find((j) => (!site || j.site === site || j.site === null)
    && ["queued", "preparing", "diffed", "approved", "deploying"].includes(j.status));
  useEffect(() => {
    if (!rollout) return;
    const id = window.setInterval(deploys.reload, ROLLOUT_MS);
    return () => window.clearInterval(id);
  }, [rollout?.id, deploys.reload]);

  const { tiles, attention, history } = summarize({
    site,
    devices: devices.data ?? [],
    drift: drift.data?.devices ?? [],
    topology: topology.error ? null : topology.data ?? { nodes: [], links: [], telemetry: { ok: true, error: null } },
    deploys: deploys.data ?? [],
    overview: overview.data,
  });
  const ports = overview.data?.ports ?? [];
  const errors = [devices.error, drift.error, deploys.error].filter(Boolean);

  return (
    <div className="overview">
      {errors.length > 0 && <p className="error">Some data could not be loaded: {errors.join("; ")}</p>}
      {overview.data && !overview.data.telemetry.ok && (
        <p className="warning">Telemetry unavailable ({overview.data.telemetry.error}): alerts and traffic are not shown.</p>
      )}

      <section className={`strip${health.data ? " n6" : ""}`} aria-label="Fabric health">
        {health.data && (
          <a className={`tile score-tile ${health.data.level === "good" ? "good" : health.data.level === "warning" ? "warn" : "bad"}`} href="#/overview/health">
            <span className="label">Health</span>
            <span className="value num">{health.data.score}<small>/100</small></span>
            <span className="sub">{health.data.worst ? `worst: ${health.data.worst.name}` : "nothing wrong"}</span>
          </a>
        )}
        {tiles.map((t) => (
          <div key={t.key} className={`tile ${t.tone}`}>
            <span className="label"><span className="ico">{ICONS[TILE_ICONS[t.key]]}</span>{t.label}</span>
            <span className="value">
              <Counter value={t.value} />
              {t.of !== undefined && <small>/{t.of}</small>}
            </span>
            <span className="sub">{t.sub}</span>
          </div>
        ))}
      </section>

      {/* Layout A: the map in the middle, what needs you on the right. */}
      <div className="cols-map">
        <TopologyView site={site} deploy={rollout ? Object.fromEntries(rollout.steps.map((s) => [s.device, s.status])) : undefined} />
        <div className="side">
          <section className="panel" aria-label="Needs attention">
            <header>
              <h2>Needs you</h2>
              <span className="hint">{attention.length || "nothing"}</span>
            </header>
            {attention.length === 0 ? (
              <p className="empty">Everything is in order: switches match intent, every active switch is polled, nothing waits for approval.</p>
            ) : (
              <ul className="tasks">
                {attention.map((a, i) => (
                  <li key={i}>
                    <span className={`ico ${a.tone}`}>{ICONS[a.tone === "info" ? "deploys" : a.tone === "bad" ? "drift" : "alerts"]}</span>
                    <div>
                      <strong>{a.title}</strong>
                      <span>{a.detail}</span>
                    </div>
                    <a className={`button${a.tone === "info" ? " primary" : ""}`} href={a.href}>
                      {a.action}
                    </a>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section className="panel" aria-label="Deploy history">
            <header>
              <h2>Last deploys</h2>
              <a className="hint" href="#/changes/history">all deploys</a>
            </header>
            {history.length === 0 ? (
              <p className="empty">No deploy yet.</p>
            ) : (
              <ul className="timeline">
                {history.slice(0, 3).map((j) => (
                  <li key={j.id}>
                    <span className={`dot ${deployTone(j.status)}`} />
                    <div>
                      <a href={`#/changes/deploys/${j.id}`}>
                        <strong>#{j.id}</strong>
                      </a>{" "}
                      <span className={`pill deploy-${j.status}`}>{j.status}</span>
                      <span className="sub">
                        {j.steps.length} device{j.steps.length === 1 ? "" : "s"}
                        {j.generators ? ` · ${j.generators.join(", ")}` : ""} · by {j.requested_by ?? "unknown"}
                      </span>
                    </div>
                    <time>{when(j.created_at)}</time>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>

      <section className="panel" aria-label="Busiest ports">
        <header>
          <h2>Busiest ports</h2>
          <span className="hint">physical ports, now · trend over 15 min</span>
        </header>
        {ports.length === 0 ? (
          <p className="empty">{overview.data?.telemetry.ok === false ? "Telemetry unavailable." : "No traffic data yet."}</p>
        ) : (
          <div className="tablewrap">
            <table className="devices">
              <thead>
                <tr>
                  <th>Port</th>
                  <th>In</th>
                  <th>Out</th>
                  <th>Load</th>
                  <th>Trend</th>
                </tr>
              </thead>
              <tbody>
                {ports.map((p) => {
                  const pct = load(p);
                  return (
                    <tr key={`${p.device} ${p.interface}`}>
                      <td>
                        <a className="mono" href={`#/switches/${encodeURIComponent(p.device)}`}>{p.interface}</a>
                        <div className="faint small">{p.device}</div>
                      </td>
                      <td className="num">{formatBps(p.in_bps)}</td>
                      <td className="num">{formatBps(p.out_bps)}</td>
                      <td>
                        {pct === null ? (
                          <span className="faint">—</span>
                        ) : (
                          <div className="load">
                            <span className="track"><i style={{ width: `${pct}%` }} /></span>
                            <span className="num faint">{pct < 1 ? "<1" : Math.round(pct)} %</span>
                          </div>
                        )}
                      </td>
                      <td><Sparkline values={p.history} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
