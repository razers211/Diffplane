import { useState } from "react";
import {
  approveImportKeys, previewImport, readImport, startImport, writeImport,
  type ImportPlan, type ImportPreview, type ImportSession, type ImportSwitch, type OverrideEntry,
} from "./api";
import { KeyRows } from "./HostKeys";
import {
  changed, emptyDraft, importReport, offeredKeys, parseAddresses, toChoices, type ImportDraft, type SwitchReport,
} from "./importFabric";
import { POOL_LABELS } from "./newFabric";
import { useCan } from "./role";

// Import an existing fabric: the switches' management addresses, their
// host keys compared and approved, each switch read over SSH (display commands only), then a
// preview of the fabric they make and of the patch each would get. Nothing is written before
// Import; deploys stay off for the new fabric until an admin turns them on.

function Kept({ entries }: { entries: OverrideEntry[] }) {
  return (
    <ul>
      {entries.map((e) => (
        <li key={e.path}>
          {e.scope !== "switch" && e.target !== null && <b>{e.scope === "segment" ? `Segment VNI ${e.target}` : e.target}: </b>}
          {e.text} <span className="hint">Usual form: {e.derived}.{e.standard ? " Can be made standard after the import." : ""}</span>
        </li>
      ))}
    </ul>
  );
}

// One switch of the import report: what would change on it, what it keeps as found, what the
// platform leaves alone there.
function SwitchCard({ r }: { r: SwitchReport }) {
  const lines = r.patch.trim() ? r.patch.trim().split("\n").length : 0;
  return (
    <div className="panel">
      <header>
        <h3 className="mono">{r.name}</h3>
        <span className="inline small">
          {lines ? <span className="pill warnpill">would change</span> : <span className="pill good">reproduced exactly</span>}
          {r.kept.length > 0 && <span className="pill">{r.kept.length} kept as found</span>}
          {r.misfits.length > 0 && <span className="pill warnpill">{r.misfits.length} not managed</span>}
        </span>
      </header>
      <div className="pad">
        {r.misfits.length > 0 && (
          <>
            <p className="small"><b>Not managed</b>: the platform's model has no place for these, so it leaves them alone (or, where
              said, the first deploy removes them).</p>
            <ul>{r.misfits.map((m) => <li key={m}>{m}</li>)}</ul>
          </>
        )}
        {r.kept.length > 0 && (
          <>
            <p className="small"><b>Kept as found</b>: these differ from what the platform would make, and stay as they are.</p>
            <Kept entries={r.kept} />
          </>
        )}
        {lines > 0 ? (
          <details>
            <summary>What its first deploy would send ({lines} lines)</summary>
            <pre className="config">{r.patch}</pre>
          </details>
        ) : <p className="muted small">The platform's config for this switch is the one it runs: no patch.</p>}
      </div>
    </div>
  );
}

/** The import report: the fabric's own findings, then one card per switch read. */
export function ImportReport({ plan, entries, patches }: { plan: ImportPlan; entries: OverrideEntry[]; patches: Record<string, string> }) {
  const changes = changed(patches).length;
  const report = importReport(plan, entries, patches);
  return (
    <div className="import-report" data-tour="import-report">
      <h3>Report</h3>
      <p className={changes ? "muted" : "notice"}>
        {changes === 0 ? "Every switch read is reproduced exactly: importing changes nothing on them."
          : `${changes} of ${report.switches.length} switches would change at their first deploy; the rest are reproduced exactly.`}
        {" "}Nothing is deployed by the import itself.
      </p>
      {(report.kept.length > 0 || report.misfits.length > 0) && (
        <div className="panel">
          <header><h3>The fabric</h3></header>
          <div className="pad">
            {report.misfits.length > 0 && (
              <>
                <p className="small"><b>Not managed</b></p>
                <ul>{report.misfits.flatMap(([where, list]) => list.map((m) => <li key={where + m}><b>{where}</b>: {m}</li>))}</ul>
              </>
            )}
            {report.kept.length > 0 && (<><p className="small"><b>Kept as found</b></p><Kept entries={report.kept} /></>)}
          </div>
        </div>
      )}
      {report.switches.map((r) => <SwitchCard key={r.name} r={r} />)}
    </div>
  );
}

const clean = (e: unknown) => (e instanceof Error ? e.message : String(e)).replace(/^\d+: /, "");

function SwitchRow({ s, session, onSession }: { s: ImportSwitch; session: string; onSession: (x: ImportSession) => void }) {
  const canApprove = useCan("approver");
  const [compared, setCompared] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const approve = async () => {
    setError(null);
    try {
      onSession(await approveImportKeys(session, s.address, offeredKeys(s)));
    } catch (e) {
      setError(clean(e));
    }
  };
  return (
    <tr>
      <td className="mono">{s.address}</td>
      <td className="mono">{s.sysname ?? <span className="faint">not read</span>}</td>
      <td>
        <KeyRows keys={(s.offered["22"] ?? []).concat((s.offered["830"] ?? []).filter((k) => !(s.offered["22"] ?? []).some((x) => x.key === k.key)))} />
        {!s.same_on_both_ports && Object.keys(s.offered).length > 0 && <p className="warning small">Ports 22 and 830 present different keys.</p>}
      </td>
      <td>
        {s.approved ? <span className="pill good">approved by {s.approved_by}</span>
          : canApprove && Object.keys(s.offered).length > 0 ? (
            <span className="inline">
              <label className="check small">
                <input type="checkbox" checked={compared} onChange={(e) => setCompared(e.target.checked)} />
                compared with the console
              </label>
              <button className="small primary" disabled={!compared} onClick={approve}>Approve keys</button>
            </span>
          ) : <span className="muted small">{canApprove ? "" : "an approver compares and approves"}</span>}
        {s.read && <span className="pill good">read</span>}
        {s.in_netbox && <span className="pill">in NetBox: preview only</span>}
        {(error ?? s.error) && <p className="error small">{error ?? s.error}</p>}
      </td>
    </tr>
  );
}

export function ImportFabric({ onCreated }: { onCreated: (site: string) => void }) {
  const canOperate = useCan("operator");
  const [addresses, setAddresses] = useState("");
  const [session, setSession] = useState<ImportSession | null>(null);
  const [draft, setDraft] = useState<ImportDraft>(emptyDraft);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const set = (d: Partial<ImportDraft>) => { setDraft({ ...draft, ...d }); setPreview(null); };

  const step = async (what: string, call: () => Promise<void>) => {
    setError(null);
    setBusy(what);
    try {
      await call();
    } catch (e) {
      setError(clean(e));
    } finally {
      setBusy(null);
    }
  };
  if (!canOperate) return <section><p className="muted">Importing a fabric needs the operator role.</p></section>;

  const allRead = !!session && session.switches.every((s) => s.read);
  const plan = preview?.plan;
  const patches = preview?.patches ?? {};
  const toChange = changed(patches);
  const pairs = plan?.pairs.map((p) => p.devices.join("+")) ?? [];

  return (
    <section className="import-fabric">
      <div className="toolbar">
        <a href="#/fabric">← Fabric</a>
        <h1>Import fabric</h1>
      </div>
      <p className="muted">
        Bring a running fabric under the platform. Each switch is read over SSH with <code>display</code> commands only,
        after an approver has compared its host key with its console. The preview shows the fabric the switches make and
        the patch each would get; an empty patch means the platform reproduces the switch exactly. Nothing is written
        before <b>Import</b>, and deploys stay off for the new fabric until an admin turns them on.
      </p>
      {error && <p className="error">{error}</p>}

      <div className="panel">
        <header data-tour="import-switches"><h2>1. Switches</h2><span className="hint">management addresses with their mask</span></header>
        {!session ? (
          <div className="form-row">
            <label>Addresses
              <textarea className="mono" rows={4} value={addresses} onChange={(e) => setAddresses(e.target.value)}
                placeholder={"172.24.0.104/24\n172.24.0.105/24"} />
            </label>
            <button className="primary" disabled={!parseAddresses(addresses).length || !!busy}
              onClick={() => step("start", async () => setSession(await startImport(parseAddresses(addresses))))}>
              {busy === "start" ? "Fetching keys…" : "Fetch host keys"}
            </button>
          </div>
        ) : (
          <>
            <p className="muted small">
              Compare each fingerprint with the switch's own display (<code>display rsa local-key-pair public</code>)
              before approving. A switch whose keys are not approved is never logged into.
            </p>
            <table className="devices">
              <thead><tr><th>Address</th><th>Sysname</th><th>Host key</th><th>Status</th></tr></thead>
              <tbody>
                {session.switches.map((s) => <SwitchRow key={s.address} s={s} session={session.id} onSession={setSession} />)}
              </tbody>
            </table>
            <div className="inline">
              <button className="primary" disabled={!session.switches.some((s) => s.approved) || !!busy}
                onClick={() => step("read", async () => setSession(await readImport(session.id)))}>
                {busy === "read" ? "Reading…" : "Read approved switches"}
              </button>
              <button onClick={() => { setSession(null); setPreview(null); }}>Start again</button>
            </div>
          </>
        )}
      </div>

      {allRead && session && (
        <div className="panel">
          <header><h2>2. Fabric</h2><span className="hint">empty fields: as found on the switches</span></header>
          <div className="form-row build-form">
            <label>Name<input value={draft.name} onChange={(e) => set({ name: e.target.value })} placeholder="dc2" /></label>
            <label>Architecture
              <select value={draft.architecture} onChange={(e) => set({ architecture: e.target.value as ImportDraft["architecture"] })}>
                <option value="">as found{plan ? ` (${plan.architecture})` : ""}</option>
                <option value="symmetric">symmetric</option>
                <option value="centralized">centralized</option>
              </select>
            </label>
            {draft.architecture === "centralized" && (
              <label>Gateway pair
                <select value={draft.gatewayPair} onChange={(e) => set({ gatewayPair: e.target.value })}>
                  <option value="">choose</option>
                  {pairs.map((p) => <option key={p} value={p}>{p.replace("+", " + ")}</option>)}
                </select>
              </label>
            )}
            <label>Gateway MAC
              <input className="mono" value={draft.gatewayMac} onChange={(e) => set({ gatewayMac: e.target.value })}
                placeholder={plan?.gateway_mac ?? "only if no VBDIF says it"} />
            </label>
          </div>
          <div className="form-row build-form">
            {POOL_LABELS.map(([key, label]) => (
              <label key={key}>{label.split(" (")[0]}
                <input className="mono" value={draft.pools[key] ?? ""} placeholder={plan?.pools[key] ?? (key === "gateways" ? "required" : "")}
                  onChange={(e) => set({ pools: { ...draft.pools, [key]: e.target.value } })} />
              </label>
            ))}
          </div>
          <button className="primary" disabled={!draft.name.trim() || !!busy}
            onClick={() => step("preview", async () => setPreview(await previewImport(session.id, toChoices(draft))))}>
            {busy === "preview" ? "Building the preview…" : "Preview"}
          </button>
        </div>
      )}

      {preview && session && (
        <div className="panel" data-tour="import-preview">
          <header><h2>3. Preview</h2><span className="hint">nothing written yet</span></header>
          {preview.error && <p className="error">{preview.error}</p>}
          {plan && (
            <>
              <p>
                ASN <b className="mono">{plan.asn}</b> · {plan.architecture}
                {plan.gateway_pair && <> · gateway pair {plan.gateway_pair.join(" + ")}</>} · {plan.devices.length} switches
                ({plan.devices.filter((d) => d.planned).length} planned) · {plan.links.length} links · {plan.segments.length} segments ·{" "}
                {plan.services.length} services
              </p>
              <table className="devices">
                <thead><tr><th>Switch</th><th>Role</th><th>Model</th><th>Management</th><th>LoopBack0</th><th>VTEP</th></tr></thead>
                <tbody>
                  {plan.devices.map((d) => (
                    <tr key={d.name}>
                      <td className="mono">{d.name}{d.planned && <span className="pill">planned</span>}</td>
                      <td>
                        <select value={draft.roles[d.name] ?? d.role ?? ""} onChange={(e) => set({ roles: { ...draft.roles, [d.name]: e.target.value } })}>
                          {!d.role && <option value="">unknown</option>}
                          {["spine", "leaf", "border-leaf"].map((r) => <option key={r} value={r}>{r}</option>)}
                        </select>
                      </td>
                      <td>{d.model}</td>
                      <td className="mono">{d.management ?? "—"}</td>
                      <td className="mono">{d.loopback ?? "—"}</td>
                      <td className="mono">{d.vtep ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
          {plan && preview.patches && <ImportReport plan={plan} entries={preview.entries ?? []} patches={patches} />}
          {plan && !preview.error && session.switches.some((s) => s.in_netbox) && (
            <p className="muted small">A switch here is already in NetBox: this import can be previewed, not written.</p>
          )}
          {plan && !preview.error && !session.switches.some((s) => s.in_netbox) && (
            <div className="inline">
              <button className="primary" disabled={!!busy}
                onClick={() => step("write", async () => onCreated((await writeImport(session.id, toChoices(draft))).site))}>
                {busy === "write" ? "Importing…" : "Import"}
              </button>
              {toChange.length > 0 && <span className="muted small">The changes above show as drift once imported; nothing is deployed.</span>}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
