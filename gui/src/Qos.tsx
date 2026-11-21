import { useEffect, useState } from "react";
import { fetchDevices, fetchPorts, fetchQos, fetchSegments, setQosEnabled, setQosPort, setQosSegment, type Segment } from "./api";
import { draggable, dropZone, installDragDrop, useDrops } from "./dragdrop";
import { colourOf } from "./ports";
import { MARKS, planRows, portRows, type QosMark } from "./qos";
import { useCan } from "./role";
import { useFetch } from "./useFetch";

// Network › QoS: on or off per fabric; once on, every port of every switch
// gets one queue plan and trusts DSCP. Segments and ports can be marked storage or scavenger.
// Everything here is intent: it shows in the pending bar and is deployed as usual.

const message = (e: unknown) => (e instanceof Error ? e.message.replace(/^\d+: /, "") : String(e));

function MarkSelect({ value, disabled, onChange, id }: { value: QosMark | null; disabled: boolean; id?: string;
                                                        onChange: (mark: QosMark | null) => void }) {
  return (
    <select id={id} value={value ?? ""} disabled={disabled} onChange={(ev) => onChange((ev.target.value || null) as QosMark | null)}>
      <option value="">no mark (hosts' DSCP)</option>
      {MARKS.map((m) => <option key={m.mark} value={m.mark}>{m.label}</option>)}
    </select>
  );
}

// The segment marks as lanes: a segment dragged (or tapped, then the lane
// tapped) into a lane takes its mark.
function Lanes({ segments, marks, disabled, onMark }: {
  segments: Segment[]; marks: Record<string, QosMark>; disabled: boolean; onMark: (s: Segment, m: QosMark | null) => void;
}) {
  useEffect(installDragDrop, []);
  useDrops({
    "qos-lane": (p, target) => {
      const s = segments.find((x) => x.vni === Number(p.vni));
      const m = (target || null) as QosMark | null;
      if (s && (marks[String(s.vni)] ?? null) !== m) onMark(s, m);
    },
  });
  const lanes: { mark: QosMark | null; label: string; text: string }[] = [
    { mark: null, label: "No mark", text: "the hosts' own DSCP" }, ...MARKS,
  ];
  return (
    <div className="qos-lanes">
      {lanes.map((l) => (
        <div key={l.label} className={`qos-lane${l.mark ? ` m-${l.mark}` : ""}`} tabIndex={disabled ? -1 : 0} role="group" aria-label={`${l.label} lane`}
             {...(disabled ? {} : dropZone("qos-lane", "qseg", l.mark ?? ""))}>
          <header><b>{l.label}</b><span className="faint small">{l.text}</span></header>
          <div className="qos-chips">
            {segments.filter((s) => (marks[String(s.vni)] ?? null) === l.mark).map((s) => (
              <button key={s.vni} className={`seg-chip s${colourOf(segments, s.vni)}`} disabled={disabled}
                      {...(disabled ? {} : draggable({ type: "qseg", label: s.name, vni: s.vni }))}>
                <i />{s.name}<small>{s.vni}</small>
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

export function Qos({ site }: { site: string }) {
  const canOperate = useCan("operator");
  const view = useFetch(() => (site ? fetchQos(site) : Promise.resolve(null)), [site]);
  const segments = useFetch(() => (site ? fetchSegments(site) : Promise.resolve([])), [site]);
  const devices = useFetch(() => (site ? fetchDevices(site) : Promise.resolve([])), [site]);
  const [device, setDevice] = useState("");
  const ports = useFetch(() => (device ? fetchPorts(device) : Promise.resolve([])), [device]);
  const [port, setPort] = useState("");
  const [mark, setMark] = useState<QosMark | null>("storage");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  if (!site) return <p className="empty muted">Pick a fabric (top right): QoS belongs to one fabric.</p>;
  if (view.error) return <p className="error">{view.error}</p>;
  const data = view.data;
  if (!data) return <p className="muted">Loading…</p>;

  const act = async (work: () => Promise<unknown>, done: string) => {
    setError(null);
    setNotice(null);
    try {
      await work();
      setNotice(`${done} It reaches the switches at the next deploy.`);
      view.reload();
      return true;
    } catch (e) {
      setError(message(e));
      return false;
    }
  };
  const label = (m: QosMark | null) => MARKS.find((x) => x.mark === m)?.label ?? "no mark";
  const leaves = (devices.data ?? []).filter((d) => d.role === "leaf" || d.role === "border-leaf");
  const marked = portRows(data.ports);

  return (
    <div className="security-page">
      <section className="panel" data-tour="qos-mode">
        <header>
          <h2>QoS</h2>
          <span className="hint">one queue plan on every port · DSCP trusted · marks per segment or port</span>
        </header>
        <div className="mode-pills" role="radiogroup" aria-label="QoS">
          {[false, true].map((on) => (
            <button key={String(on)} role="radio" aria-checked={data.enabled === on} className={data.enabled === on ? "pill-on" : ""}
              disabled={!canOperate || data.enabled === on}
              onClick={() => act(() => setQosEnabled(site, on), `QoS ${on ? "on" : "off"}.`)}>
              {on ? "On" : "Off"}
            </button>
          ))}
          <span className="muted">
            {data.enabled ? "Every front-panel port of every switch has the plan below; marks apply." : "Nothing on the switches. Marks are kept and apply once QoS is on."}
          </span>
        </div>
        {error && <p className="error">{error}</p>}
        {notice && <p className="notice">{notice}</p>}
      </section>

      <section className="panel" data-tour="qos-plan">
        <header>
          <h2>Queue plan</h2>
          <span className="hint">every port, leaves and spines · Huawei's default DSCP mapping</span>
        </header>
        <table className="devices">
          <thead><tr><th className="num">Queue</th><th>Class</th><th>Traffic</th><th>Scheduling</th><th className="num">Weight</th></tr></thead>
          <tbody>
            {planRows(data.plan).map((r) => (
              <tr key={r.queue}>
                <td className="num mono">{r.queue}</td>
                <td className="mono">{r.cls}</td>
                <td>{r.traffic}</td>
                <td>{r.mode === "PQ" ? <span className="pill good">strict priority</span> : "weighted"}</td>
                <td className="num mono">{r.weight === null ? "" : `${r.weight} (${r.share} %)`}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="faint small">Strict-priority queues are served first; weighted queues share what is left by weight.</p>
      </section>

      <section className="panel" data-tour="qos-segments">
        <header>
          <h2>Segment marks</h2>
          <span className="hint">everything entering the segment gets the mark's DSCP</span>
        </header>
        {segments.error && <p className="error">{segments.error}</p>}
        {(segments.data ?? []).length === 0 && <p className="empty muted">No segment yet.</p>}
        <Lanes segments={(segments.data ?? []).filter((s) => s.fabric)} marks={data.segments} disabled={!canOperate}
          onMark={(s, m) => act(() => setQosSegment(site, s.vni, m), `${s.name}: ${label(m)}.`)} />
      </section>

      <section className="panel" data-tour="qos-ports">
        <header>
          <h2>Port marks</h2>
          <span className="hint">everything entering a port or trunk · an M-LAG trunk on both leaves</span>
        </header>
        {marked.length > 0 && (
          <table className="devices">
            <thead><tr><th>Switch</th><th>Port</th><th>Mark</th><th /></tr></thead>
            <tbody>
              {marked.map((r) => (
                <tr key={`${r.device} ${r.port}`}>
                  <td className="mono">{r.device}</td>
                  <td className="mono">{r.port}</td>
                  <td>{label(r.mark)}</td>
                  <td>{canOperate && <button onClick={() => act(() => setQosPort(site, r.device, r.port, null), `${r.device} ${r.port}: no mark.`)}>Remove</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {canOperate && (
          <div className="form-row">
            <label htmlFor="q-switch">Switch
              <select id="q-switch" value={device} onChange={(ev) => { setDevice(ev.target.value); setPort(""); }}>
                <option value="">a leaf…</option>
                {leaves.map((d) => <option key={d.name} value={d.name}>{d.name}</option>)}
              </select>
            </label>
            <label htmlFor="q-port">Port
              <select id="q-port" value={port} disabled={!device} onChange={(ev) => setPort(ev.target.value)}>
                <option value="">a port or trunk…</option>
                {(ports.data ?? []).map((p) => <option key={p.name} value={p.name}>{p.name}{p.mlag ? " (M-LAG)" : ""}</option>)}
              </select>
            </label>
            <label htmlFor="q-mark">Mark
              <MarkSelect id="q-mark" value={mark} disabled={false} onChange={setMark} />
            </label>
            <button className="primary" disabled={!device || !port}
              onClick={() => act(() => setQosPort(site, device, port, mark), `${device} ${port}: ${label(mark)}.`)}>Save mark</button>
          </div>
        )}
        <p className="faint small">Trunk members, routed fabric links and the peer-link take no mark: mark the trunk instead.</p>
      </section>
    </div>
  );
}
