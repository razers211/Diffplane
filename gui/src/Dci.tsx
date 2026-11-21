import { useState } from "react";
import { deleteDciLink, deleteDciSegment, fetchDci, fetchDevices, fetchFabrics, fetchSegments, setDciLink, setDciSegment } from "./api";
import { commonSegments, uplinkOf, waiting, type DciLink, type DciSide } from "./dci";
import { useCan } from "./role";
import { useFetch } from "./useFetch";

// Fabric › DC links: EVPN DCI between two fabrics. Each side is an M-LAG
// pair (its border) with uplinks to the other DC or a provider; chosen segments stretch with the same
// name on both sides. A link spans fabrics, so only approvers change it. Everything here is intent.

const message = (e: unknown) => (e instanceof Error ? e.message.replace(/^\d+: /, "") : String(e));

interface Row { port: string; address: string; neighbour: string; asn: string }
interface SideForm { fabric: string; a: string; b: string; rows: Record<string, Row> }
const blankRow: Row = { port: "", address: "", neighbour: "", asn: "" };

function sideForm(side?: DciSide, fabric = ""): SideForm {
  const rows: Record<string, Row> = {};
  for (const [sw, items] of Object.entries(side?.uplinks ?? {})) {
    const u = items[0];
    if (u) rows[sw] = { port: u.port, address: u.address, neighbour: u.neighbour, asn: String(u.asn) };
  }
  return { fabric: side?.fabric ?? fabric, a: side?.pair[0] ?? "", b: side?.pair[1] ?? "", rows };
}

function SideFields({ label, form, onChange, fabrics, devices }: {
  label: string; form: SideForm; onChange: (f: SideForm) => void; fabrics: string[];
  devices: { name: string; site: string | null; role: string | null }[];
}) {
  const choices = devices.filter((d) => d.site === form.fabric).map((d) => d.name).sort();
  const row = (sw: string) => form.rows[sw] ?? blankRow;
  const setRow = (sw: string, r: Row) => onChange({ ...form, rows: { ...form.rows, [sw]: r } });
  return (
    <fieldset className="dci-side">
      <legend>{label}</legend>
      <div className="form-row">
        <label>Fabric
          <select value={form.fabric} onChange={(ev) => onChange({ ...form, fabric: ev.target.value, a: "", b: "", rows: {} })}>
            <option value="">a fabric…</option>
            {fabrics.map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
        </label>
        {(["a", "b"] as const).map((k) => (
          <label key={k}>{k === "a" ? "Border switch" : "Its M-LAG peer"}
            <select value={form[k]} disabled={!form.fabric} onChange={(ev) => onChange({ ...form, [k]: ev.target.value })}>
              <option value="">a switch…</option>
              {choices.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
        ))}
      </div>
      {[form.a, form.b].filter(Boolean).map((sw) => (
        <div className="form-row" key={sw}>
          <span className="mono small">{sw} uplink</span>
          <input aria-label={`${sw} port`} placeholder="100GE1/0/3" value={row(sw).port} onChange={(ev) => setRow(sw, { ...row(sw), port: ev.target.value })} spellCheck={false} />
          <input aria-label={`${sw} address`} placeholder="10.255.10.1/30" value={row(sw).address} onChange={(ev) => setRow(sw, { ...row(sw), address: ev.target.value })} spellCheck={false} />
          <input aria-label={`${sw} neighbour`} placeholder="neighbour 10.255.10.2" value={row(sw).neighbour} onChange={(ev) => setRow(sw, { ...row(sw), neighbour: ev.target.value })} spellCheck={false} />
          <input aria-label={`${sw} neighbour ASN`} placeholder="its ASN" value={row(sw).asn} onChange={(ev) => setRow(sw, { ...row(sw), asn: ev.target.value })} inputMode="numeric" />
        </div>
      ))}
    </fieldset>
  );
}

function Stretch({ link, waitingFor, onDone, act }: {
  link: DciLink; waitingFor: string[]; onDone: () => void; act: (w: () => Promise<unknown>, done: string) => Promise<boolean>;
}) {
  const [fa, fb] = link.sides.map((s) => s.fabric);
  const a = useFetch(() => fetchSegments(fa), [fa]);
  const b = useFetch(() => fetchSegments(fb), [fb]);
  const [choice, setChoice] = useState("");
  const offered = commonSegments(a.data ?? [], b.data ?? []).filter((s) => !link.segments.some((x) => x.name === s.name));
  const picked = offered.find((s) => s.name === choice);
  const blocked = waitingFor.length > 0;
  return (
    <div className="form-row">
      <label>Stretch a segment
        <select value={choice} disabled={blocked} onChange={(ev) => setChoice(ev.target.value)}>
          <option value="">{offered.length ? "a segment both fabrics have…" : "no segment of the same name in both fabrics"}</option>
          {offered.map((s) => <option key={s.name} value={s.name}>{s.name} ({fa} {s.a}, {fb} {s.b})</option>)}
        </select>
      </label>
      <button className="primary" disabled={blocked || !picked}
        onClick={async () => picked && (await act(() => setDciSegment(link.name, picked.name, { [fa]: picked.a, [fb]: picked.b }),
                                                   `${picked.name} stretched over ${link.name}.`)) && onDone()}>Stretch</button>
      {!blocked && <span className="hint">The DCI VNI is picked for you: the lowest free from 60000.</span>}
      {blocked && <span className="hint">Deploy the link first: {waitingFor.join(", ")} not verified with it yet.</span>}
    </div>
  );
}

export function Dci({ site }: { site: string }) {
  const canApprove = useCan("approver");
  const view = useFetch(() => (canApprove ? fetchDci() : Promise.resolve(null)), [canApprove]);
  const fabrics = useFetch(() => fetchFabrics(), []);
  const devices = useFetch(() => fetchDevices(), []);
  const [form, setForm] = useState<{ name: string; split: string; sides: [SideForm, SideForm] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  if (!canApprove) return <p className="empty muted">DC links join two fabrics, so only approvers see and change them.</p>;
  if (view.error) return <p className="error">{view.error}</p>;
  const data = view.data;
  if (!data) return <p className="muted">Loading…</p>;
  const active = new Set((devices.data ?? []).filter((d) => d.status === "active").map((d) => d.name));
  const fabricNames = (fabrics.data ?? []).map((f) => f.site).sort();

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
  const save = async () => {
    if (!form) return;
    const sides: DciSide[] = [];
    for (const s of form.sides) {
      const uplinks: DciSide["uplinks"] = {};
      for (const sw of [s.a, s.b].filter(Boolean)) {
        const u = uplinkOf(s.rows[sw] ?? blankRow);
        if (typeof u === "string") return setError(u);
        if (u.length) uplinks[sw] = u;
      }
      sides.push({ fabric: s.fabric, pair: [s.a, s.b], uplinks });
    }
    if (await act(() => setDciLink(form.name.trim(), sides, form.split.trim() || "DCI"), `DC link ${form.name} saved.`)) setForm(null);
  };

  return (
    <div className="security-page">
      <section className="panel" data-tour="dci-links">
        <header>
          <h2>DC links</h2>
          <span className="hint">EVPN between two fabrics' border pairs · MAC and MAC/IP routes re-originated · chosen segments stretched</span>
          <button onClick={() => setForm({ name: "", split: "DCI", sides: [sideForm(undefined, site), sideForm()] })}>New link</button>
        </header>
        {error && <p className="error">{error}</p>}
        {notice && <p className="notice">{notice}</p>}
        {data.links.length === 0 && !form && <p className="empty muted">No DC link yet.</p>}
        {form && (
          <div className="dci-form">
            <div className="form-row">
              <label>Link name<input value={form.name} onChange={(ev) => setForm({ ...form, name: ev.target.value })} placeholder="east-west" /></label>
              <label>Split group<input value={form.split} onChange={(ev) => setForm({ ...form, split: ev.target.value })} /></label>
            </div>
            {form.sides.map((s, i) => (
              <SideFields key={i} label={i === 0 ? "This side" : "Other DC"} form={s} fabrics={fabricNames} devices={devices.data ?? []}
                onChange={(f) => setForm({ ...form, sides: (i === 0 ? [f, form.sides[1]] : [form.sides[0], f]) as [SideForm, SideForm] })} />
            ))}
            <p className="faint small">An uplink is a free physical port: a /30 or /31, eBGP to the other DC's border (dark fibre) or the provider's router (L3 VPN), which must carry 1600-byte packets.</p>
            <div className="form-row">
              <button className="primary" onClick={save}>Save link</button>
              <button onClick={() => setForm(null)}>Cancel</button>
            </div>
          </div>
        )}
      </section>

      {data.links.map((link) => {
        const wait = waiting(link, data.ready, active);
        return (
          <section className="panel" key={link.name} data-tour="dci-segments">
            <header>
              <h2 className="mono">{link.name}</h2>
              <span className="hint">{link.sides.map((s) => `${s.fabric} (${s.pair.join(" + ")})`).join(" ↔ ")} · split group {link.split_group}</span>
              <button onClick={() => setForm({ name: link.name, split: link.split_group, sides: [sideForm(link.sides[0]), sideForm(link.sides[1])] })}>Edit</button>
              <button onClick={() => act(() => deleteDciLink(link.name), `DC link ${link.name} deleted.`)}>Delete</button>
            </header>
            <table className="devices">
              <thead><tr><th>Border</th><th>Uplink</th><th>Address</th><th>Neighbour</th><th className="num">ASN</th><th>Deployed</th></tr></thead>
              <tbody>
                {link.sides.flatMap((s) => s.pair.map((sw) => {
                  const u = s.uplinks[sw]?.[0];
                  return (
                    <tr key={sw}>
                      <td className="mono">{sw}</td>
                      <td className="mono">{u?.port ?? "–"}</td>
                      <td className="mono">{u?.address ?? ""}</td>
                      <td className="mono">{u?.neighbour ?? ""}</td>
                      <td className="num mono">{u?.asn ?? ""}</td>
                      <td>{!active.has(sw) ? <span className="faint small">planned</span>
                        : data.ready.includes(sw) ? <span className="pill good">verified</span> : <span className="pill warnpill">not yet</span>}</td>
                    </tr>
                  );
                }))}
              </tbody>
            </table>
            <h3>Stretched segments</h3>
            {link.segments.length === 0 && <p className="empty muted">None yet.</p>}
            {link.segments.length > 0 && (
              <table className="devices">
                <thead><tr><th>Segment</th>{link.sides.map((s) => <th key={s.fabric} className="num">VNI in {s.fabric}</th>)}<th className="num">DCI VNI</th><th /></tr></thead>
                <tbody>
                  {link.segments.map((g) => (
                    <tr key={g.name}>
                      <td className="mono">{g.name}</td>
                      {link.sides.map((s) => <td key={s.fabric} className="num mono">{g.vnis[s.fabric]}</td>)}
                      <td className="num mono">{g.dci_vni}</td>
                      <td><button onClick={() => act(() => deleteDciSegment(link.name, g.name), `${g.name} no longer stretched.`)}>Stop stretching</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <Stretch link={link} waitingFor={wait} act={act} onDone={() => view.reload()} />
          </section>
        );
      })}
    </div>
  );
}
