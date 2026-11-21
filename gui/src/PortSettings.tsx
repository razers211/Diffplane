import { useEffect, useRef, useState } from "react";
import { ApplyHint } from "./Draft";
import {
  fetchPortAdoption, fetchPortSetting, fetchPortSettingsManaged, savePortSetting, setPortSettingsManaged,
  type PortAdoption, type PortValues,
} from "./api";
import { useCan } from "./role";
import { useFetch } from "./useFetch";

// Port settings: a server port's or trunk's name and on/off in plain words,
// a copper port's speed (Auto or fixed), the frame size behind Expert. Managed per fabric: turning it on first adopts what the switches have.

const message = (e: unknown) => (e instanceof Error ? e.message.replace(/^\d+: /, "") : String(e));
const DEFAULT_FRAME = 9216;
// Copper ports only: Auto (negotiated, the default) or one fixed speed.
const SPEEDS: [number | null, string][] = [[null, "Auto"], [10, "10M"], [100, "100M"], [1000, "1G"], [10000, "10G"]];
const speedText = (s: number | null) => SPEEDS.find(([v]) => v === s)?.[1] ?? `${s}M`;
const describe = (v: PortValues) =>
  [v.description ? `“${v.description}”` : "no name", v.enabled ? "on" : "off", v.frame ? `${v.frame}-byte frames` : null,
   v.speed ? `fixed at ${speedText(v.speed)}` : null]
    .filter(Boolean).join(", ");

/** Whether this fabric's port settings are managed; turning them on shows what is adopted first. */
export function PortSettingsManaged({ site, onChanged }: { site: string; onChanged: () => void }) {
  const canOperate = useCan("operator");
  const managed = useFetch(() => fetchPortSettingsManaged(site), [site]);
  const [preview, setPreview] = useState<PortAdoption | null>(null);
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const act = async (work: () => Promise<unknown>) => {
    setError(null);
    setBusy(true);
    try {
      await work();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };
  const turn = (on: boolean) => act(async () => {
    await setPortSettingsManaged(site, on);
    setPreview(null);
    setAsking(false);
    managed.reload();
    onChanged();
  });

  if (!managed.data) return null;
  if (managed.data.enabled) {
    return (
      <p className="muted small port-managed" data-tour="ports-managed">
        Port names and on/off are managed here: click a port or trunk to change them.{" "}
        {canOperate && !asking && <button className="linklike small" onClick={() => setAsking(true)}>Stop managing</button>}
        {asking && (
          <span className="confirm-inline" role="alertdialog" aria-label="Confirm">
            Stop? Nothing changes on the switches; their names and on/off are then left as they are.
            <button className="danger small" disabled={busy} onClick={() => turn(false)}>Yes, stop</button>
            <button className="small" onClick={() => setAsking(false)}>Keep</button>
          </span>
        )}
        {error && <span className="error"> {error}</span>}
      </p>
    );
  }
  return (
    <section className="panel port-adopt" data-tour="ports-managed">
      <header><h2>Port names and on/off</h2><span className="hint">not managed in {site} yet</span></header>
      <div className="pad">
        <p>
          To name ports and turn them off from here, the platform first takes over what the switches have today.
          Read them to see what it would take over; nothing changes on any switch.
        </p>
        {canOperate && !preview && <button disabled={busy} onClick={() => act(async () => setPreview(await fetchPortAdoption(site)))}>
          {busy ? "Reading the switches…" : "Read the switches"}</button>}
        {preview && (
          <>
            <p>
              {preview.switches.length} switch{preview.switches.length === 1 ? "" : "es"} read, {preview.ports} server ports and trunks.{" "}
              {preview.changes.length === 0 ? "Nothing to take over: every port has the defaults." : `${preview.changes.length} to take over:`}
            </p>
            {preview.changes.length > 0 && (
              <div className="table-wrap">
                <table className="dense">
                  <thead><tr><th>Switch</th><th>Port</th><th>On the switch</th></tr></thead>
                  <tbody>{preview.changes.map((c) => (
                    <tr key={`${c.device} ${c.port}`}><td>{c.device}</td><td className="mono">{c.port}</td><td>{describe(c.switch)}</td></tr>
                  ))}</tbody>
                </table>
              </div>
            )}
            <div className="actions">
              <button className="primary" disabled={busy} onClick={() => turn(true)}>Take over and manage</button>
              <button onClick={() => setPreview(null)}>Cancel</button>
            </div>
            <p className="faint small">The switches are read again when you confirm. Afterwards the first diff is empty.</p>
          </>
        )}
        {error && <p className="error">{error}</p>}
      </div>
    </section>
  );
}

/** One port's or trunk's settings, under the front panel. */
export function PortSettingsPanel({ device, port, refresh, onSaved }: {
  device: string; port: string; refresh: number; onSaved: (text: string) => void;
}) {
  const canOperate = useCan("operator");
  const shown = useFetch(() => fetchPortSetting(device, port), [device, port, refresh]);
  const [name, setName] = useState("");
  const [frame, setFrame] = useState("");
  const [expert, setExpert] = useState(false);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const data = shown.data;
  const loaded = useRef<{ name: string; frame: string } | null>(null);
  useEffect(() => {
    // A reload (after another setting's save) replaces only fields the user has not edited since the last load.
    const next = { name: data?.description ?? "", frame: data?.frame ? String(data.frame) : "" };
    const before = loaded.current;
    setName((n) => (before && n !== before.name && n.trim() !== next.name ? n : next.name));   // a saved name comes back trimmed
    setFrame((f) => (before && f !== before.frame ? f : next.frame));
    loaded.current = next;
    setAsking(false);
    setError(null);
  }, [data]);
  useEffect(() => { loaded.current = null; }, [device, port]);

  if (shown.error) return <p className="error small">{shown.error}</p>;
  if (!data) return null;
  if (data.refusal) return <p className="muted small">No port settings: {data.refusal.replace(`${device} ${port}`, "this").replace(/^this: /, "")}.</p>;
  if (!data.managed) return <p className="muted small">Port names and on/off are not managed in this fabric yet (see the top of the page).</p>;
  const where = data.both.length > 1 ? `on ${data.both.join(" and ")}` : `on ${device}`;

  const save = async (change: Parameters<typeof savePortSetting>[2], text: string) => {
    setError(null);
    setBusy(true);
    try {
      await savePortSetting(device, port, change);
      onSaved(text);
      shown.reload();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };
  const frameValue = frame.trim() === "" ? null : Number(frame);
  const frameBad = frameValue !== null && !(Number.isInteger(frameValue) && frameValue >= 1518 && frameValue <= 15360);

  return (
    <div className="port-settings" data-tour="port-settings">
      <h4>Port settings{data.both.length > 1 ? " · both switches" : ""}</h4>
      <div className="form-row">
        <label htmlFor="port-name">
          Name
          <input id="port-name" value={name} maxLength={242} placeholder="e.g. web-01 NIC 1" disabled={!canOperate}
                 onChange={(e) => setName(e.target.value)} />
        </label>
        {canOperate && name.trim() !== data.description && (
          <button className="primary" disabled={busy || name.includes("?")} title={name.includes("?") ? "a name cannot hold '?'" : undefined}
                  onClick={() => save({ description: name }, `${port} ${where} named “${name.trim()}”`)}>Save name</button>
        )}
      </div>
      <div className="form-row">
        <span>Port is</span>
        <span className="toggle" role="group" aria-label="On or off">
          <button aria-pressed={data.enabled} disabled={!canOperate || busy || data.enabled}
                  onClick={() => save({ enabled: true }, `${port} ${where} turned on`)}>On</button>
          <button aria-pressed={!data.enabled} disabled={!canOperate || busy || !data.enabled}
                  onClick={() => (data.carries.length ? setAsking(true) : save({ enabled: false }, `${port} ${where} turned off`))}>Off</button>
        </span>
      </div>
      {data.copper && (
        <div className="form-row">
          <span>Speed</span>
          <span className="toggle" role="group" aria-label="Speed">
            {SPEEDS.map(([value, label]) => (
              <button key={label} aria-pressed={data.speed === value} disabled={!canOperate || busy || data.speed === value}
                      onClick={() => save({ speed: value }, `${port} ${where}: speed ${value ? `fixed at ${label}` : "auto"}`)}>{label}</button>
            ))}
          </span>
          <span className="muted small">Auto agrees on a speed with the other end. A fixed speed needs the same fixed speed on the server.</span>
        </div>
      )}
      {asking && (
        <div className="confirm-inline" role="alertdialog" aria-label="Confirm">
          <span>Turning this off stops {data.carries.join(", ")} {where}. Turn off?</span>
          <button className="danger" disabled={busy} onClick={() => save({ enabled: false }, `${port} ${where} turned off`)}>Yes, turn off</button>
          <button onClick={() => setAsking(false)}>Keep it on</button>
        </div>
      )}
      <button className="linklike small" aria-expanded={expert} onClick={() => setExpert(!expert)}>{expert ? "Hide Expert" : "Expert"}</button>
      {expert && (
        <div className="form-row">
          <label htmlFor="port-frame">
            Largest frame (bytes)
            <input id="port-frame" className="mono narrow" inputMode="numeric" value={frame} placeholder={String(DEFAULT_FRAME)}
                   disabled={!canOperate} onChange={(e) => setFrame(e.target.value)} />
          </label>
          {canOperate && frameValue !== data.frame && (
            <button disabled={busy || frameBad} title={frameBad ? "1518 to 15360, or empty for the switch's 9216" : undefined}
                    onClick={() => save({ frame: frameValue }, `${port} ${where}: ${frameValue ? `${frameValue}-byte frames` : "default frame size"}`)}>
              Save
            </button>
          )}
          <span className="muted small">Empty: the switch's default, 9216.</span>
        </div>
      )}
      <p className="faint small">Saved as a change: <ApplyHint /></p>
      {error && <p className="error">{error}</p>}
    </div>
  );
}
