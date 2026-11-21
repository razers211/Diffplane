import { useEffect, useState } from "react";
import {
  fetchDevices, fetchProtection, fetchTrunks, setPortProtection, setProtectionMode,
  type ProtectionMode, type ProtectionTemplate,
} from "./api";
import { Faceplate } from "./Faceplate";
import { cancelPick, draggable, dropZone, installDragDrop, useDrops } from "./dragdrop";
import { trunkOrPort, type FacePort } from "./frontPanel";
import { effects, MODES, protectedRows, switchLines, TEMPLATES, tripsOf } from "./protection";
import { useCan } from "./role";
import { useFetch } from "./useFetch";

// Network › Port protection (from the approved prototype): Off / Watch / On
// for the fabric, and a template (Server, Hypervisor, None) dragged onto a server port or trunk of
// the drawn front panel. Every write is intent; nothing reaches a switch before a deploy.

const message = (e: unknown) => (e instanceof Error ? e.message.replace(/^\d+: /, "") : String(e));
const COLOUR: Record<ProtectionTemplate, string> = { server: "var(--p-segment)", hypervisor: "var(--p-trunk)" };
const labelOf = (t: ProtectionTemplate | null) => TEMPLATES.find((x) => x.template === t)!.label;

/** Why a port of the drawing takes no template (the API has the last word: VLAN trunks, DCI uplinks...). */
function refuse(port: FacePort): string | null {
  switch (port.kind) {
    case "fabric": return "a link to another switch: never protected";
    case "peer": return "the M-LAG peer-link: never protected";
    case "service": return `${port.detail ?? "a service"} faces a firewall or ISP: never protected`;
    default: return null;
  }
}

export function Protection({ site }: { site: string }) {
  if (!site) return <p className="empty muted">Pick a fabric (top right): port protection belongs to one fabric.</p>;
  return <Page key={site} site={site} />;
}

function Page({ site }: { site: string }) {
  useEffect(installDragDrop, []);
  useEffect(() => cancelPick, []);
  const canOperate = useCan("operator");
  const view = useFetch(() => fetchProtection(site), [site]);
  const devices = useFetch(() => fetchDevices(site), [site]);
  const switches = (devices.data ?? []).filter((d) => d.site === site).sort((a, b) => a.name.localeCompare(b.name));
  const [chosen, setChosen] = useState("");
  const [version, setVersion] = useState(0);
  const device = switches.some((d) => d.name === chosen) ? chosen : switches.find((d) => d.role !== "spine")?.name ?? switches[0]?.name ?? "";
  const trunks = useFetch(() => (device ? fetchTrunks(device) : Promise.resolve(null)), [device, version]);
  const trunkData = trunks.data;
  const edgeTrunks = (trunkData?.trunks ?? []).filter((t) => t.kind !== "peer-link" && !t.service);
  const isMlag = (name: string) => edgeTrunks.some((t) => t.name === name && t.kind === "m-lag");
  const [selected, setSelected] = useState<string | null>(null);
  const [asking, setAsking] = useState<ProtectionMode | null>(null);
  const [expert, setExpert] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string[]>([]);
  useEffect(() => setSelected(null), [device]);
  useEffect(() => {   // what protection does now follows the collector's 25 s control poll
    const id = window.setInterval(view.reload, 25_000);
    return () => window.clearInterval(id);
  }, [view.reload]);

  const write = async (work: () => Promise<unknown>, text: string) => {
    setError(null);
    setBusy(true);
    try {
      await work();
      setSaved((s) => [text, ...s]);
      view.reload();
      setVersion((v) => v + 1);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };
  const protect = (port: string, template: ProtectionTemplate | null) =>
    write(async () => { await setPortProtection(device, port, template); setSelected(port); }, `${device} ${port}${trunkData?.peer && isMlag(port) ? ` and ${trunkData.peer}` : ""}: ${labelOf(template)}`);

  useDrops({ protect: (p, target) => { if (canOperate) void protect(target, (p.template || null) as ProtectionTemplate | null); } });

  if (view.error) return <p className="error">{view.error}</p>;
  if (devices.error) return <p className="error">{devices.error}</p>;
  const data = view.data;
  if (!data || !devices.data) return <p className="muted">Loading…</p>;

  const mine = data.ports[device] ?? {};
  const members = Object.fromEntries((trunkData?.trunks ?? []).map((t) => [t.name, t.members]));
  const templateOf = (port: FacePort) => mine[trunkOrPort(port)];
  const lines = switchLines(data.mode, data.plan, mine, members);
  const chosenTemplate = selected ? mine[selected] ?? null : null;
  const trips = (port: string) => tripsOf(data.tripped ?? [], device, port, members[port] ?? []);
  const selectedTrips = selected ? trips(selected) : [];
  const rows = protectedRows(data.ports);

  const askMode = (m: ProtectionMode) => (m === "off" || data.mode === "off" ? setAsking(m) : write(() => setProtectionMode(site, m), `Port protection ${m}`));
  const askText = asking === "off"
    ? "Turn protection Off? Every protection line the platform wrote is removed at the next deploy; the templates are kept."
    : `Turn protection to ${asking === "on" ? "On" : "Watch"}? Storm control, loop detection or edge-port lines made by hand on server ports show as removals in the next deploy's patch.`;

  return (
    <div className="security-page protection-page">
      <section className="panel" data-tour="protection-mode">
        <header><h2>How the fabric reacts</h2><span className="hint">every protected port of {site}</span></header>
        <div className="pad">
          <div className="protect-modes" role="radiogroup" aria-label="Port protection">
            {MODES.map((m) => (
              <button key={m.mode} role="radio" aria-checked={data.mode === m.mode} className={`protect-mode m-${m.mode}`}
                      disabled={!canOperate || busy || data.mode === m.mode} onClick={() => askMode(m.mode)}>
                <b>{m.label}</b><span>{m.mode === "on" ? m.text.replace("a pause", `${data.plan.recovery} s`) : m.text}</span>
              </button>
            ))}
          </div>
          {asking && (
            <div className="confirm-box" role="alertdialog" aria-label="Confirm">
              <span>{askText}</span>
              <button className="primary" disabled={busy} onClick={() => { const m = asking; setAsking(null); void write(() => setProtectionMode(site, m), `Port protection ${m}`); }}>Yes</button>
              <button onClick={() => setAsking(null)}>Cancel</button>
            </div>
          )}
          {error && <p className="error">{error}</p>}
        </div>
      </section>

      {(data.tripped ?? []).length > 0 && (
        <section className="panel protect-tripped" data-tour="protection-tripped" role="status">
          <header><h2>Acting now</h2><span className="hint">from the switches, every 25 s</span></header>
          <ul>{data.tripped.map((t) => <li key={`${t.device} ${t.port} ${t.cause}`}><b>{t.device}</b> {t.text}</li>)}</ul>
        </section>
      )}

      <section className="panel" data-tour="protection-templates">
        <header>
          <h2>Templates</h2>
          <span className="hint">{canOperate ? "Drag one onto a port or a trunk, or tap it, then tap the port" : "Read only"}</span>
        </header>
        <div className="protect-templates">
          {TEMPLATES.map((t) => (
            <button key={t.label} className="protect-template" disabled={!canOperate}
                    {...(canOperate ? draggable({ type: "shield", label: t.label, template: t.template ?? "" }) : {})}>
              <Shield colour={t.template ? COLOUR[t.template] : "var(--faint)"} />
              <span><b>{t.label}</b><small>{t.text}</small></span>
            </button>
          ))}
        </div>
      </section>

      <div className="ports-pick" data-tour="protection-switch">
        <label>Switch{" "}
          <select value={device} onChange={(e) => setChosen(e.target.value)}>
            {switches.map((d) => <option key={d.name} value={d.name}>{d.name}{d.status !== "active" ? ` (${d.status})` : ""}</option>)}
          </select>
        </label>
        {trunkData?.peer && <span className="muted small">M-LAG pair with {trunkData.peer}: an M-LAG trunk is protected on both</span>}
      </div>

      {device && (
        <section className="panel switch-ports" data-tour="protection-panel">
          <header><h2>{device}</h2><span className="hint">Coloured: protected · click a port to see what it does</span></header>
          <div className="pad">
            <Faceplate
              device={device}
              refresh={version}
              zone="protect"
              accept="shield"
              legend={false}
              drop={canOperate ? (p) => (refuse(p) ? null : trunkOrPort(p)) : undefined}
              paint={(p) => (trips(p.name).length ? "var(--bad)" : refuse(p) ? undefined : templateOf(p) ? COLOUR[templateOf(p)!] : "transparent")}
              pick={{
                selected: selected ? [selected] : [],
                refuse,
                target: trunkOrPort,
                onToggle: (t) => setSelected(selected === t ? null : t),
              }}
            />
            <div className="fp-legend">
              <span><i style={{ background: COLOUR.server }} />Server</span>
              <span><i style={{ background: COLOUR.hypervisor }} />Hypervisor</span>
              <span><i className="k-free" />not protected</span>
              <span><i className="k-fabric" />link to another switch</span>
              <span><i className="k-peer" />M-LAG peer-link</span>
              <span><i className="k-service" />firewall or ISP</span>
              <span><i style={{ background: "var(--bad)" }} />protection acting now</span>
            </div>
            {edgeTrunks.length > 0 && (
              <div className="trunk-drops">
                {edgeTrunks.map((t) => (
                  <button key={t.name} className={`trunk-drop${selected === t.name ? " sel" : ""}`} onClick={() => setSelected(selected === t.name ? null : t.name)}
                          style={trips(t.name).length ? { borderLeftColor: "var(--bad)" } : mine[t.name] ? { borderLeftColor: COLOUR[mine[t.name]] } : undefined}
                          {...(canOperate ? dropZone("protect", "shield", t.name) : {})}>
                    <b>{t.name}</b><span>{t.kind === "m-lag" ? "M-LAG" : "single"} · {t.members.join(", ")}</span>
                    <span>{trips(t.name).length ? "protection acting now" : mine[t.name] ? labelOf(mine[t.name]) : "not protected"}</span>
                  </button>
                ))}
              </div>
            )}
            {selected && (
              <div className="port-inspect" data-tour="protection-port">
                <h3>{selected}{isMlag(selected) ? " · M-LAG trunk, both leaves" : ""}</h3>
                <span className="toggle" role="group" aria-label="Template">
                  {TEMPLATES.map((t) => (
                    <button key={t.label} aria-pressed={chosenTemplate === t.template} disabled={!canOperate || busy || chosenTemplate === t.template}
                            onClick={() => protect(selected, t.template)}>{t.label}</button>
                  ))}
                </span>
                {selectedTrips.map((t) => <p key={`${t.port} ${t.cause}`} className="error small">Now: {t.text}.</p>)}
                {chosenTemplate && data.mode !== "off" && (
                  <ul className="small protect-effects">{effects(data.mode, chosenTemplate, data.plan).map((e) => <li key={e}>{e}</li>)}</ul>
                )}
                {chosenTemplate && data.mode === "off" && <p className="muted small">Template kept, but protection is Off: nothing is written.</p>}
                {!chosenTemplate && <p className="muted small">Not protected.</p>}
              </div>
            )}
          </div>
        </section>
      )}

      <section className="panel" data-tour="protection-list">
        <header><h2>Protected ports</h2><span className="hint">every switch of {site}</span></header>
        {rows.length === 0 ? <p className="empty muted">No port protected yet.</p> : (
          <table className="devices">
            <thead><tr><th>Switch</th><th>Port</th><th>Template</th><th /></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={`${r.device} ${r.port}`}>
                  <td className="mono">{r.device}</td><td className="mono">{r.port}</td><td>{labelOf(r.template)}</td>
                  <td>{canOperate && <button className="small" disabled={busy}
                        onClick={() => write(() => setPortProtection(r.device, r.port, null), `${r.device} ${r.port}: None`)}>Remove</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="panel" data-tour="protection-lines">
        <header>
          <h2>What {device} gets</h2>
          <button className="linklike small" aria-expanded={expert} onClick={() => setExpert(!expert)}>{expert ? "Hide Expert view" : "Expert view"}</button>
        </header>
        <div className="pad">
          <p>{data.mode === "off"
            ? "Nothing: protection is Off. Lines the platform wrote earlier are removed at the next deploy."
            : `${Object.keys(mine).length} port${Object.keys(mine).length === 1 ? "" : "s"} and trunks protected (${data.mode === "on" ? "On" : "Watch"}).`}</p>
          {expert && <pre className="config">{lines.join("\n") || "(no lines)"}</pre>}
        </div>
      </section>

      {saved.length > 0 && (
        <section className="panel ports-saved">
          <header><h2>Saved this visit</h2><a className="button primary" href="#/changes">Review and deploy</a></header>
          <ul>{saved.map((t, i) => <li key={i}>{t}</li>)}</ul>
        </section>
      )}
    </div>
  );
}

function Shield({ colour }: { colour: string }) {
  return (
    <svg className="shield" viewBox="0 0 18 20" aria-hidden="true">
      <path d="M9 1 2 4v5c0 4.5 3 8.3 7 10 4-1.7 7-5.5 7-10V4z" style={{ fill: colour }} />
      <path d="m5.5 10 2.5 2.5 4.5-5" style={{ stroke: "var(--surface)" }} strokeWidth="1.8" fill="none" />
    </svg>
  );
}
