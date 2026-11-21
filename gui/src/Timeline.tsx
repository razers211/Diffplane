import { useEffect, useState } from "react";
import { fetchDevices, fetchEvents, type TimelineEvent } from "./api";
import { href } from "./nav";
import { useFetch } from "./useFetch";

// "What changed": state changes the orchestrator's event watcher saw (every
// 30 s), and deploys and intent changes from the audit trail, newest first. Kept 90 days.

const REFRESH_MS = 30_000;
const KINDS: [string, string][] = [
  ["", "everything"], ["link", "links"], ["bgp", "BGP"], ["ospf", "OSPF"], ["mlag", "M-LAG"], ["hardware", "hardware"],
  ["optic", "optics"], ["polling", "polling"], ["alert", "alerts"], ["drift", "drift"], ["health", "health"],
  ["change", "deploys and intent"], ["window", "change windows"],
];
const SPANS: [string, number][] = [["last hour", 1], ["last 24 hours", 24], ["last 7 days", 168], ["last 90 days", 2160]];
const TONE = { problem: "bad", recovered: "good", change: "info" } as const;

export function eventTitle(e: TimelineEvent): string {
  if (e.kind === "change") return e.subject.replace(".", " ");
  if (e.kind === "alert") return `${e.subject} ${e.state}`;
  if (e.kind === "health") return `${e.device ? "health" : "fabric health"} ${e.previous ?? "?"} → ${e.state}`;
  return `${e.subject}: ${e.previous ? `${e.previous} → ` : ""}${e.state}`;
}

function when(iso: string): string {
  const d = new Date(iso);
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  return d.toDateString() === new Date().toDateString() ? time : `${d.toLocaleDateString()} ${time}`;
}

export function Timeline({ site, device: fixed }: { site: string; device?: string }) {
  const [device, setDevice] = useState(fixed ?? "");
  const [kind, setKind] = useState("");
  const [hours, setHours] = useState(24);
  const devices = useFetch(() => (fixed ? Promise.resolve([]) : fetchDevices(site || undefined)), [site, fixed]);
  const data = useFetch(() => fetchEvents({
    site: site || undefined, device: device || undefined, kind: kind || undefined,
    since: new Date(Date.now() - hours * 3600_000).toISOString(), limit: 500,
  }), [site, device, kind, hours]);
  useEffect(() => {
    const id = window.setInterval(data.reload, REFRESH_MS);
    return () => window.clearInterval(id);
  }, [data.reload]);
  const events = data.data ?? [];

  return (
    <section className="panel timeline-page">
      <header data-tour="timeline-filters">
        <h2>What changed</h2>
        {!fixed && (
          <select aria-label="Switch" value={device} onChange={(e) => setDevice(e.target.value)}>
            <option value="">every switch</option>
            {(devices.data ?? []).filter((d) => d.status === "active").map((d) => <option key={d.name} value={d.name}>{d.name}</option>)}
          </select>
        )}
        <select aria-label="Kind" value={kind} onChange={(e) => setKind(e.target.value)}>
          {KINDS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
        </select>
        <select aria-label="Time span" value={hours} onChange={(e) => setHours(Number(e.target.value))}>
          {SPANS.map(([label, h]) => <option key={h} value={h}>{label}</option>)}
        </select>
        <span className="hint">{events.length} event{events.length === 1 ? "" : "s"}</span>
      </header>
      {data.error && <p className="error">{data.error}</p>}
      {data.data && events.length === 0 && <p className="empty muted">Nothing changed in this span.</p>}
      <ul className="timeline" data-tour="timeline">
        {events.map((e, i) => (
          <li key={`${e.at}-${i}`} className={`ev-${e.severity}`}>
            <span className={`dot ${TONE[e.severity]}`} />
            <div>
              <strong>{eventTitle(e)}</strong>
              {e.device && <> on <a className="mono" href={href.switchPage(e.device, e.kind === "hardware" || e.kind === "optic" ? "hardware"
                : ["bgp", "ospf", "mlag"].includes(e.kind) ? "control" : "ports")}>{e.device}</a></>}
              {e.job_id !== null && <> · <a href={href.deploy(e.job_id)}>deploy #{e.job_id}</a></>}
              <span className="sub">
                {[e.kind === "change" ? `by ${e.user ?? "?"}` : e.kind, e.detail].filter(Boolean).join(" · ")}
              </span>
            </div>
            <time>{when(e.at)}</time>
          </li>
        ))}
      </ul>
    </section>
  );
}
