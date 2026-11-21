import { useState } from "react";
import { fetchDevices, fetchDrift, fetchTopology, type DriftState } from "./api";
import { ICONS } from "./icons";
import { placeLabel } from "./location";
import { useCan } from "./role";
import { naturalCompare } from "./topology";
import { Tiles } from "./Tiles";
import { useFetch } from "./useFetch";

// Devices (polish): every switch with what an operator checks first: is it built, is it
// polled, does it match intent.

const DRIFT: Record<DriftState, [string, string]> = {
  in_sync: ["in sync", "pill good"],
  out_of_sync: ["out of sync", "pill warnpill"],
  error: ["check failed", "pill bad"],
  unknown: ["not checked", "pill"],
};
const ROLES = ["spine", "border-leaf", "leaf"];

export function DeviceList({ site }: { site: string }) {
  const devices = useFetch(() => fetchDevices(site || undefined), [site]);
  const drift = useFetch(fetchDrift, []);
  const topology = useFetch(() => fetchTopology(site || undefined), [site]);
  const [search, setSearch] = useState("");
  const [role, setRole] = useState("");
  const canOperate = useCan("operator");

  const all = [...(devices.data ?? [])].sort((a, b) => naturalCompare(a.name, b.name));
  const states = new Map((drift.data?.devices ?? []).map((d) => [d.device, d.state]));
  const polled = new Set((topology.data?.nodes ?? []).filter((n) => n.collected).map((n) => n.name));
  const active = all.filter((d) => d.status === "active");
  const inSync = active.filter((d) => states.get(d.name) === "in_sync");
  const rows = all.filter(
    (d) => (!role || d.role === role) && (!search || `${d.name} ${d.primary_ip ?? ""} ${d.location ?? ""} ${d.rack ?? ""}`.toLowerCase().includes(search.toLowerCase())),
  );

  return (
    <div className="page">
      {devices.data && (
        <Tiles
          label="Devices"
          tiles={[
            { key: "all", label: "Switches", value: all.length, sub: ROLES.map((r) => `${all.filter((d) => d.role === r).length} ${r}`).filter((t) => !t.startsWith("0 ")).join(" · ") || "none", tone: "info", icon: "devices" },
            { key: "active", label: "Built", value: active.length, of: all.length, sub: `${all.length - active.length} planned (modelled only)`, tone: "info", icon: "overview" },
            { key: "polled", label: "Polled", value: active.filter((d) => polled.has(d.name)).length, of: active.length, sub: topology.error ? "telemetry unavailable" : "by the collector, every 5 s", tone: topology.error || active.some((d) => !polled.has(d.name)) ? "warn" : "good", icon: "topology" },
            { key: "sync", label: "In sync", value: inSync.length, of: active.length, sub: "match their intent", tone: inSync.length === active.length ? "good" : "warn", icon: "drift" },
          ]}
        />
      )}
      <section>
        <div className="toolbar">
          <h1 className="sr-only">Switches</h1>
          {canOperate && site && <a className="button primary" href="#/switches/add" data-tour="add-switches">Add switches</a>}
        </div>
        <div className="filters" data-tour="switch-filters">
          <input id="device-search" placeholder="Search name, address or rack" value={search} onChange={(e) => setSearch(e.target.value)} />
          <select id="device-role" value={role} onChange={(e) => setRole(e.target.value)} aria-label="Role">
            <option value="">every role</option>
            {ROLES.map((r) => (
              <option key={r} value={r}>{r}</option>
            ))}
          </select>
          <span className="count">{rows.length} of {all.length}</span>
        </div>
        {devices.error && <p className="error">Could not load devices: {devices.error}</p>}
        {devices.loading && !devices.data && <p className="muted">Loading devices…</p>}
        {devices.data && all.length === 0 && <p className="muted">No devices{site ? ` in fabric "${site}"` : ""}.</p>}
        {rows.length > 0 && (
          <table className="devices" data-tour="switch-list">
            <thead>
              <tr>
                <th>Device</th>
                <th>Role</th>
                <th>Status</th>
                <th>Drift</th>
                <th>Collector</th>
                <th>Management</th>
                <th>Location</th>
                {!site && <th>Fabric</th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((d) => {
                const state = d.status === "active" ? states.get(d.name) ?? "unknown" : null;
                return (
                  <tr key={d.name} className="clickable" onClick={() => (window.location.hash = `#/switches/${encodeURIComponent(d.name)}`)}>
                    <td>
                      <a className="mono" href={`#/switches/${encodeURIComponent(d.name)}`}>{d.name}</a>
                    </td>
                    <td>
                      <span className="role-chip">{ICONS[d.role === "spine" ? "topology" : "devices"]}{d.role ?? "—"}</span>
                    </td>
                    <td><span className={`pill status-${d.status}`}>{d.status}</span></td>
                    <td>{state ? <span className={DRIFT[state][1]}>{DRIFT[state][0]}</span> : <span className="faint">—</span>}</td>
                    <td>
                      {d.status !== "active" ? <span className="faint">—</span> : polled.has(d.name) ? <span className="pill good">polled</span> : <span className="pill warnpill">not polled</span>}
                    </td>
                    <td className="mono">{d.primary_ip ?? <span className="faint">—</span>}</td>
                    <td className="small">{d.rack ? placeLabel(d) : <span className="faint">—</span>}</td>
                    {!site && <td>{d.site}</td>}
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
