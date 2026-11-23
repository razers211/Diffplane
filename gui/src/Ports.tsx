import { useEffect, useRef, useState } from "react";
import {
  attachSegment, createTrunk, detachSegment, fetchArchitecture, fetchDevices, fetchPorts, fetchSegments, fetchTrunks,
  type Segment,
} from "./api";
import { Faceplate } from "./Faceplate";
import { PortSettingsManaged, PortSettingsPanel } from "./PortSettings";
import { cancelPick, draggable, dropZone, installDragDrop, useDrops } from "./dragdrop";
import { refusal, trunkOrPort } from "./frontPanel";
import { attachableLeaves } from "./placement";
import { alreadyOn, colourOf, presetVid, splitTarget, targetOf, untaggedBlocker } from "./ports";
import { useCan } from "./role";
import { trunkProblem } from "./trunkForm";
import { useFetch } from "./useFetch";
import { parseVid, vidLabel } from "./vid";

// Network › Ports: segments dragged onto a switch's front panel or its
// trunks, free ports clicked together into a new Eth-Trunk. A pair's two switches side by side.
// Every switch is offered: spines and border leaves take trunks and port
// settings here too; segments are dropped only where they may go (`attachableLeaves`).
// Every write is ordinary intent (the pending bar follows); nothing reaches a switch before a deploy.

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

interface Ask { vni: number; device: string; iface: string; x: number; y: number }

export function PortsBoard({ site }: { site: string }) {
  if (!site) return <p className="empty muted">Pick a fabric (top right): segments belong to one fabric.</p>;
  return <Board key={site} site={site} />;
}

function Board({ site }: { site: string }) {
  useEffect(installDragDrop, []);
  useEffect(() => cancelPick, []);
  const canOperate = useCan("operator");
  const [version, setVersion] = useState(0);
  const devices = useFetch(() => fetchDevices(site), [site]);
  const arch = useFetch(() => fetchArchitecture(site), [site]);
  const segs = useFetch(() => fetchSegments(site), [site, version]);
  const leaves = [...(devices.data ?? [])].sort((a, b) => a.name.localeCompare(b.name));
  const carries = new Set(attachableLeaves(devices.data ?? [], arch.data?.architecture === "centralized").map((d) => d.name));
  const [chosen, setChosen] = useState("");
  const device = leaves.some((l) => l.name === chosen) ? chosen : leaves[0]?.name ?? "";
  const peer = (devices.data ?? []).find((d) => d.name === device)?.peer ?? null;
  const shown = [device, peer].filter((d): d is string => !!d).sort();
  const [ask, setAsk] = useState<Ask | null>(null);
  const [saved, setSaved] = useState<string[]>([]);
  const segments = (segs.data ?? []).filter((s) => s.fabric === site);

  useDrops({ port: (p, target, at) => setAsk({ vni: Number(p.vni), ...splitTarget(target), ...at }) });
  const onSaved = (text: string) => { setSaved((s) => [text, ...s]); setVersion((v) => v + 1); };

  if (devices.error) return <p className="error">{devices.error}</p>;
  if (!devices.data) return <p className="muted">Loading…</p>;
  if (leaves.length === 0) return <p className="muted">{site} has no switch yet.</p>;

  return (
    <div className="ports-board">
      <PortSettingsManaged site={site} onChanged={() => setVersion((v) => v + 1)} />
      <section className="panel" data-tour="ports-palette">
        <header>
          <h2>Segments</h2>
          <span className="hint">{canOperate ? "Drag one onto a port or a trunk, or tap it, then tap the port" : "Read only"}</span>
        </header>
        <div className="seg-palette">
          {segments.length === 0 && <span className="muted">No segment yet: make one in Network › Segments.</span>}
          {[...segments].sort((a, b) => a.vni - b.vni).map((s) => (
            <button key={s.vni} className={`seg-chip s${colourOf(segments, s.vni)}`} disabled={!canOperate}
                    {...(canOperate ? draggable({ type: "segment", label: s.name, vni: s.vni }) : {})}
                    title={`VNI ${s.vni} · bridge domain ${s.bd_id}${s.routing ? " · routed" : ""}`}>
              <i />{s.name}<small>{s.attachments.length} port{s.attachments.length === 1 ? "" : "s"}</small>
            </button>
          ))}
        </div>
      </section>

      <div className="ports-pick" data-tour="ports-switch">
        <label>Switch{" "}
          <select value={device} onChange={(e) => setChosen(e.target.value)}>
            {leaves.map((l) => <option key={l.name} value={l.name}>{l.name}{l.status !== "active" ? ` (${l.status})` : ""}</option>)}
          </select>
        </label>
        {peer && <span className="muted small">M-LAG pair with {peer}: both shown</span>}
      </div>

      {shown.map((d) => (
        <SwitchPorts key={d} site={site} device={d} segments={carries.has(d) ? segments : null} version={version} onSaved={onSaved} />
      ))}

      {ask && <AttachAsk ask={ask} site={site} segments={segments} onClose={() => setAsk(null)} onSaved={onSaved} />}

      {saved.length > 0 && (
        <section className="panel ports-saved" data-tour="ports-saved">
          <header><h2>Saved this visit</h2><a className="button primary" href="#/changes">Review and deploy</a></header>
          <ul>{saved.map((t, i) => <li key={i}>{t}</li>)}</ul>
        </section>
      )}
    </div>
  );
}

function SwitchPorts({ site, device, segments: carried, version, onSaved }: {
  site: string; device: string; segments: Segment[] | null; version: number; onSaved: (text: string) => void;
}) {
  const canOperate = useCan("operator");
  // null: this switch carries no segments (a spine, a symmetric fabric's border leaf): trunks and port settings only.
  const segments = carried ?? [];
  const ports = useFetch(() => (carried ? fetchPorts(device) : Promise.resolve([])), [device, version, !!carried]);
  const trunks = useFetch(() => fetchTrunks(device), [device, version]);
  const [bundle, setBundle] = useState<string[]>([]);
  const [inspect, setInspect] = useState<string | null>(null);
  const [mlag, setMlag] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const keep = useRef(false);   // a port-settings save keeps the chosen port (its panel stays open)
  useEffect(() => { if (!keep.current) setBundle([]); keep.current = false; setError(null); }, [version]);
  const settingsSaved = (text: string) => { keep.current = true; onSaved(text); };

  const allowed = new Set((ports.data ?? []).map((p) => p.name));
  const byName = new Map((ports.data ?? []).map((p) => [p.name, p]));
  const name = (vni: number) => segments.find((s) => s.vni === vni)?.name ?? `VNI ${vni}`;
  const data = trunks.data;
  const both = !!data?.peer && mlag;
  const problem = data && bundle.length ? trunkProblem(data, String(data.next_number), bundle, both) : null;
  const inspected = inspect ? byName.get(inspect) : undefined;

  const run = async (write: () => Promise<unknown>, text: string) => {
    setError(null);
    setBusy(true);
    try {
      await write();
      onSaved(text);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel switch-ports">
      <header><h2>{device}</h2><span className="hint">Click free ports to bundle them; click a used port to see it{carried ? "" : " · carries no segments"}</span></header>
      <div className="pad">
        <Faceplate
          device={device}
          refresh={version}
          drop={canOperate ? (p) => (allowed.has(trunkOrPort(p)) ? targetOf(device, trunkOrPort(p)) : null) : undefined}
          paint={(p) => (p.kind === "segment" && p.segments[0] && colourOf(segments, p.segments[0].vni) ? `var(--s${colourOf(segments, p.segments[0].vni)})` : undefined)}
          pick={{
            selected: [...bundle, ...(inspect ? [inspect] : [])],
            // Any port but a fabric link or the peer-link: free ones bundle, the others show their segments and settings.
            refuse: (p) => (p.kind === "fabric" || p.kind === "peer" ? refusal(p, allowed) : null),
            target: trunkOrPort,
            onToggle: (t, p) => {
              if (p.kind === "free" && canOperate && data?.free_ports.includes(t)) {
                setInspect(null);
                setBundle((b) => (b.includes(t) ? b.filter((x) => x !== t) : [...b, t]));
              } else setInspect(inspect === t ? null : t);
            },
          }}
        />
        {data && (
          <div className="trunk-drops">
            {data.trunks.filter((t) => t.kind !== "peer-link" && !t.service).map((t) => (
              <button key={t.name} className={`trunk-drop${inspect === t.name ? " sel" : ""}`} onClick={() => setInspect(inspect === t.name ? null : t.name)}
                      {...(canOperate && allowed.has(t.name) ? dropZone("port", "segment", targetOf(device, t.name)) : {})}>
                <b>{t.name}</b><span>{t.kind === "m-lag" ? "M-LAG" : "single"} · {t.members.join(", ")}</span>
                <span>{t.segments} segment{t.segments === 1 ? "" : "s"}</span>
              </button>
            ))}
          </div>
        )}
        {bundle.length > 0 && data && (
          <div className="bundle-bar" role="group" aria-label="New Eth-Trunk">
            <span>Bundle <b className="mono">{bundle.join(", ")}</b> into <b>Eth-Trunk{data.next_number}</b></span>
            {data.peer && (
              <span className="toggle">
                <button aria-pressed={mlag} onClick={() => setMlag(true)}>M-LAG, both leaves</button>
                <button aria-pressed={!mlag} onClick={() => setMlag(false)}>Only {device}</button>
              </span>
            )}
            <button className="primary" disabled={!!problem || busy}
                    onClick={() => run(() => createTrunk(device, { number: data.next_number, members: bundle, mlag: both }),
                      `Eth-Trunk${data.next_number} (${bundle.join(", ")}) on ${both ? `${device} and ${data.peer}` : device}`)}>
              Create
            </button>
            <button onClick={() => setBundle([])}>Clear</button>
            {problem && <span className="muted small">{problem}</span>}
          </div>
        )}
        {bundle.length === 1 && !inspect && <PortSettingsPanel device={device} port={bundle[0]} refresh={version} onSaved={settingsSaved} />}
        {inspect && (
          <div className="port-inspect">
            <h3>{inspect}{inspected?.mlag ? " · M-LAG trunk, both leaves" : ""}</h3>
            {carried && !inspected?.segments.length && <p className="muted small">No segment here.{canOperate && allowed.has(inspect) ? " Drag one onto it." : ""}</p>}
            {inspected?.segments.map((s) => (
              <div key={s.vni} className="inspect-row">
                <span className={`seg-dot s${colourOf(segments, s.vni)}`} />{name(s.vni)}<span className="muted">{vidLabel(s.vid)}</span>
                {canOperate && (
                  <button className="small" disabled={busy}
                          onClick={() => run(() => detachSegment(site, s.vni, device, inspect), `${name(s.vni)} detached from ${device} ${inspect}`)}>
                    Detach
                  </button>
                )}
              </div>
            ))}
            <PortSettingsPanel device={device} port={inspect} refresh={version} onSaved={settingsSaved} />
          </div>
        )}
        {error && <p className="error">{error}</p>}
      </div>
    </section>
  );
}

function AttachAsk({ ask, site, segments, onClose, onSaved }: {
  ask: Ask; site: string; segments: Segment[]; onClose: () => void; onSaved: (text: string) => void;
}) {
  const segment = segments.find((s) => s.vni === ask.vni);
  const ports = useFetch(() => fetchPorts(ask.device), [ask.device]);
  const port = ports.data?.find((p) => p.name === ask.iface);
  const noUntagged = untaggedBlocker(port, segments);
  const [tagged, setTagged] = useState(true);
  const [vid, setVid] = useState(presetVid(segment?.bd_id ?? 0));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  if (!segment) return null;

  const attach = async () => {
    const parsed = tagged ? parseVid(vid) : { ok: true as const, vid: null };
    if (!parsed.ok) return setError(parsed.error);
    if (tagged && parsed.vid === null) return setError("Give the VLAN ID, or choose Untagged");
    setBusy(true);
    try {
      const { ports: written } = await attachSegment(site, segment.vni, ask.device, ask.iface, parsed.vid);
      onSaved(`${segment.name} on ${written.join(" and ")} (${vidLabel(parsed.vid)})`);
      onClose();
    } catch (e) {
      setError(message(e));
      setBusy(false);
    }
  };

  const left = Math.max(16, Math.min(ask.x - 150, window.innerWidth - 316));
  const top = Math.min(ask.y + 12, window.innerHeight - 220);
  return (
    <div className="attach-pop" role="dialog" aria-label={`Attach ${segment.name}`} style={{ left, top }}>
      <h3><span className={`seg-dot s${colourOf(segments, segment.vni)}`} />{segment.name} → {ask.device} {ask.iface}</h3>
      {port?.mlag && <p className="muted small">An M-LAG trunk: written on both leaves.</p>}
      {alreadyOn(port, segment.vni) ? <p className="muted">Already on this port.</p> : (
        <>
          <span className="toggle">
            <button aria-pressed={tagged} onClick={() => setTagged(true)}>Tagged</button>
            <button aria-pressed={!tagged} disabled={!!noUntagged} title={noUntagged ?? undefined} onClick={() => setTagged(false)}>Untagged</button>
          </span>
          {tagged && <label>VLAN ID <input className="mono narrow" value={vid} autoFocus onChange={(e) => setVid(e.target.value)} inputMode="numeric" /></label>}
          {noUntagged && <p className="muted small">Untagged: {noUntagged}.</p>}
        </>
      )}
      {error && <p className="error">{error}</p>}
      <div className="actions">
        {!alreadyOn(port, segment.vni) && <button className="primary" disabled={busy || !ports.data} onClick={attach}>Attach</button>}
        <button onClick={onClose}>Cancel</button>
      </div>
    </div>
  );
}
