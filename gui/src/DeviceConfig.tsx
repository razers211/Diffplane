import { Term } from "./Term";
import { Fragment, useEffect, useState } from "react";
import { fetchConfig, fetchDeviceDiff, fetchDevices, fetchHardware, fetchInterfaces, startDiffJob } from "./api";
import { href, SWITCH_TABS, type SwitchTab } from "./nav";
import { Faceplate } from "./Faceplate";
import { Routes } from "./Routes";
import { DiffBadge, DiffText } from "./DiffView";
import { jobHref, jobLabel } from "./diff";
import { useCan } from "./role";
import { formatBps, formatCount } from "./units";
import { TrafficChart } from "./TrafficChart";
import { Trunks } from "./Trunks";
import { LocationPanel } from "./LocationPanel";
import { ManagementPanel } from "./ManagementPanel";
import { SwitchHardwarePanel } from "./SwitchHardware";
import { RolePanel } from "./RolePanel";
import { ConfigHistory } from "./ConfigHistory";
import { RollbackPoints } from "./RollbackPoints";
import { ControlPlane } from "./ControlPlane";
import { Hardware, OpticsTable } from "./Hardware";
import { FlowsView } from "./Flows";
import { Logs } from "./Logs";
import { HealthBadge } from "./Health";
import { Timeline } from "./Timeline";
import { useFetch } from "./useFetch";

const COUNTERS_MS = 5_000; // the collector polls counters every 5 s

// One switch: daily things first, one-time admin last.
const TAB_LABELS: Record<SwitchTab, string> = {
  ports: "Ports", trunks: "Trunks", routing: "Routing table", control: "Control plane", hardware: "Hardware", flows: "Flows", logs: "Logs", history: "History", config: "Config and diff", settings: "Settings",
};

export function SwitchPage({ name, tab }: { name: string; tab: SwitchTab }) {
  const devices = useFetch(() => fetchDevices(), []);
  const diff = useFetch(() => fetchDeviceDiff(name), [name]);
  const device = devices.data?.find((d) => d.name === name);
  const active = device?.status === "active";

  return (
    <section className="switch-page">
      <div className="switch-head">
        <a href="#/switches">← Switches</a>
        <h1 className="mono">{name}</h1>
        {device && <span className={active ? "pill good" : "pill"}><Term k={device.status ?? ""}>{device.status ?? "unknown"}</Term></span>}
        {diff.data && <DiffBadge snap={diff.data} />}
        {device?.site && active && <HealthBadge site={device.site} name={name} />}
        {device && active && !device.pinned && <span className="pill warnpill">host keys not pinned</span>}
        {device && (
          <span className="muted">
            {[device.role, device.site, device.primary_ip].filter(Boolean).join(" · ")}
          </span>
        )}
        {devices.data && !device && <span className="error">no switch named {name}</span>}
      </div>
      <nav className="subtabs" aria-label="Switch">
        {SWITCH_TABS.map((t) => (
          <a key={t} href={href.switchPage(name, t)} className={t === tab ? "active" : ""}>{TAB_LABELS[t]}</a>
        ))}
      </nav>
      {tab === "ports" && <><PortsPanel name={name} /><Interfaces name={name} /></>}
      {tab === "trunks" && <Trunks name={name} />}
      {tab === "routing" && <Routes device={name} readable={!!device && active && device.pinned} />}
      {tab === "control" && <ControlPlane name={name} />}
      {tab === "hardware" && <Hardware name={name} />}
      {tab === "flows" && <FlowsView site={device?.site ?? ""} device={name} />}
      {tab === "logs" && <Logs name={name} />}
      {tab === "history" && <Timeline site={device?.site ?? ""} device={name} />}
      {tab === "config" && <><LatestDiff name={name} /><ConfigHistory name={name} active={active} />{device?.site && <RollbackPoints site={device.site} device={name} />}<GeneratedConfig name={name} /></>}
      {tab === "settings" && <><ManagementPanel name={name} /><SwitchHardwarePanel name={name} active={active && !!device?.pinned} onChanged={devices.reload} />{device && <RolePanel device={device} onChanged={devices.reload} />}<LocationPanel name={name} /></>}
    </section>
  );
}

// The front panel with each optical module's mark, and the optics of ports in use beside it.
function PortsPanel({ name }: { name: string }) {
  const hw = useFetch(() => fetchHardware(name).catch(() => null), [name]);
  const optics = hw.data?.optics ?? [];
  const used = optics.filter((o) => o.in_use);
  return (
    <>
      <Faceplate device={name} optics={Object.fromEntries(optics.map((o) => [o.port, o.level]))} />
      {used.length > 0 && (
        <section className="panel" data-tour="port-optics">
          <header><h2>Optics on ports in use</h2>
            <span className="hint">{optics.length - used.length} more not in use: <a href={href.switchPage(name, "hardware")}>Hardware</a></span></header>
          <OpticsTable optics={used} />
        </section>
      )}
    </>
  );
}

function GeneratedConfig({ name }: { name: string }) {
  const { data, error, loading, reload } = useFetch(() => fetchConfig(name), [name]);

  return (
    <section data-tour="generated">
      <div className="toolbar">
        <h2>Generated config</h2>
        <button onClick={reload} disabled={loading}>
          {loading ? "Generating…" : "Regenerate"}
        </button>
      </div>
      <p className="muted">
        Generated from NetBox (<code>annet gen</code>). Nothing is read from or sent to the device.
      </p>
      {error && <p className="error">{error}</p>}
      {data && <pre className="config">{data.config}</pre>}
    </section>
  );
}

function LatestDiff({ name }: { name: string }) {
  const { data, error } = useFetch(() => fetchDeviceDiff(name), [name]);
  const canOperate = useCan("operator");
  const [startError, setStartError] = useState<string | null>(null);

  const run = async () => {
    setStartError(null);
    try {
      const { id } = await startDiffJob({ devices: [name] });
      window.location.hash = `#/changes/checks/${id}`;
    } catch (e) {
      setStartError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="diff-card" data-tour="latest-diff">
      <div className="toolbar">
        <h2>Diff with device {data && <DiffBadge snap={data} />}</h2>
        {canOperate && <button onClick={run}>Run diff</button>}
      </div>
      {startError && <p className="error">{startError}</p>}
      {data ? (
        <>
          <p className="muted">
            From {data.job_kind === "deploy" ? "the verification of " : "job "}
            <a href={jobHref(data.job_kind, data.job_id)}>{jobLabel(data.job_kind, data.job_id)}</a>,{" "}
            {new Date(data.finished_at ?? data.created_at).toLocaleString()}.
          </p>
          <DiffText snap={data} />
        </>
      ) : (
        <p className="muted">{error?.startsWith("404") ? "No diff has been run for this device yet." : error}</p>
      )}
    </div>
  );
}

function Interfaces({ name }: { name: string }) {
  const { data, error, reload } = useFetch(() => fetchInterfaces(name), [name]);
  const [all, setAll] = useState(false);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    const id = window.setInterval(reload, COUNTERS_MS);
    return () => window.clearInterval(id);
  }, [reload]);

  const rows = (data?.interfaces ?? []).filter((i) => all || i.oper_up);

  return (
    <div className="diff-card">
      <div className="toolbar">
        <h2>Interfaces</h2>
        <label className="check">
          <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> show down interfaces
        </label>
      </div>
      <p className="muted">
        Traffic over the last two samples (5 s apart); errors and discards over the last 5 minutes. From the
        switch's own counters of its physical ports, read by the collector every 5 s. Click a port for its history.
      </p>
      {error && <p className="error">{error}</p>}
      {data && rows.length === 0 && <p className="muted">No interface data yet{all ? "" : " (none up)"}.</p>}
      {rows.length > 0 && (
        <table className="devices">
          <thead>
            <tr>
              <th>Interface</th>
              <th>State</th>
              <th>In</th>
              <th>Out</th>
              <th>Errors in / out</th>
              <th>Discards in / out</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((i) => (
              <Fragment key={i.interface}>
              <tr className={`clickable${open === i.interface ? " open" : ""}`} onClick={() => setOpen(open === i.interface ? null : i.interface)}>
                <td className="mono">
                  <span className="chev" aria-hidden="true">{open === i.interface ? "▾" : "▸"}</span> {i.interface}
                </td>
                <td>
                  <span className={`pill ${i.oper_up ? "good" : ""}`}>{i.oper_up ? "up" : "down"}</span>
                </td>
                <td className="mono">{formatBps(i.in_bps)}</td>
                <td className="mono">{formatBps(i.out_bps)}</td>
                <td className={`mono ${(i.in_errors ?? 0) + (i.out_errors ?? 0) >= 1 ? "error" : ""}`}>
                  {formatCount(i.in_errors)} / {formatCount(i.out_errors)}
                </td>
                <td className={`mono ${(i.in_discards ?? 0) + (i.out_discards ?? 0) >= 1 ? "error" : ""}`}>
                  {formatCount(i.in_discards)} / {formatCount(i.out_discards)}
                </td>
              </tr>
              {open === i.interface && (
                <tr className="detail">
                  <td colSpan={6}>
                    <TrafficChart device={name} iface={i.interface} />
                  </td>
                </tr>
              )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
