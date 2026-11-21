import { useEffect, useState } from "react";
import {
  approvePasswordJob, copySnmpAccess, deleteSnmpReader, deleteSnmpTrap, fetchFabrics, fetchPasswordJob, fetchSnmpAccess, fetchSnmpState,
  requestPasswordJob, setSnmpPasswords, setSnmpReader, setSnmpTrap, setSnmpV2c, type DeployJob, type SnmpSwitchState,
} from "./api";
import { useCan, useMayApprove } from "./role";
import { NAME, passwordProblem, removeBlocked, switchLines, v2cOffBlocked, READER_STATE } from "./snmpAccess";
import { useFetch } from "./useFetch";

// Monitoring access: who may read the switches over SNMP. v2c with the
// platform's community and/or SNMPv3 readers (read-only, SHA2 + AES), and trap targets sent as a
// reader. Readers and traps are intent (deployed as usual); passwords are stored write-only and typed
// on the switches by a password job, approved like a deploy, because VRP takes them only at a prompt.

const clean = (e: unknown) => (e instanceof Error ? e.message : String(e)).replace(/^\d+: /, "");
const RUNNING = ["diffed", "approved", "deploying"];

export function FabricSnmp({ site, onSaved }: { site: string; onSaved: (text: string) => void }) {
  const canOperate = useCan("operator");
  const canApprove = useMayApprove(site);
  const access = useFetch(() => fetchSnmpAccess(site), [site]);
  const fabrics = useFetch(fetchFabrics, []);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [expert, setExpert] = useState(false);
  const [newReader, setNewReader] = useState({ name: "", auth: "sha2-256", priv: "aes256" });
  const [newTrap, setNewTrap] = useState({ name: "", address: "", reader: "" });
  const [passwordsFor, setPasswordsFor] = useState<string | null>(null);
  const [pw, setPw] = useState({ auth: "", priv: "" });
  const [removing, setRemoving] = useState<string | null>(null);
  const [copyFrom, setCopyFrom] = useState("");
  const [jobId, setJobId] = useState<number | null>(null);
  const [job, setJob] = useState<DeployJob | null>(null);
  const [onSwitches, setOnSwitches] = useState<SnmpSwitchState[] | null>(null);
  const [reading, setReading] = useState(false);

  useEffect(() => {
    if (jobId === null) return;
    let stop = false;
    const tick = () => fetchPasswordJob(jobId).then((j) => { if (!stop) setJob(j); }).catch(() => undefined);
    tick();
    const timer = window.setInterval(tick, 3000);
    return () => { stop = true; window.clearInterval(timer); };
  }, [jobId]);

  const a = access.data;
  if (access.error) return <p className="error">{access.error}</p>;
  if (!a) return <p className="muted">Loading…</p>;
  const readers = Object.keys(a.readers).sort();
  const stored = new Set(a.passwords ?? []);
  const typeable = readers.filter((r) => stored.has(r));
  const others = (fabrics.data ?? []).filter((f) => f.site !== site);

  const act = async (call: () => Promise<unknown>, text: string, intent = true) => {
    setError(null);
    setBusy(true);
    try {
      await call();
      access.reload();
      if (intent) onSaved(text);
      return true;
    } catch (e) {
      setError(clean(e));
      return false;
    } finally {
      setBusy(false);
    }
  };
  const readSwitches = async () => {
    setReading(true);
    setError(null);
    try {
      setOnSwitches(await fetchSnmpState(site));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setReading(false);
    }
  };

  const addReader = async () => {
    if (await act(() => setSnmpReader(site, newReader.name, { auth: newReader.auth, priv: newReader.priv }),
      `Reader ${newReader.name} saved: its user is created at the next deploy. Then set its passwords and type them on the switches.`)) {
      setNewReader({ ...newReader, name: "" });
    }
  };
  const savePasswords = async (reader: string) => {
    if (await act(() => setSnmpPasswords(site, reader, pw.auth, pw.priv), "", false)) {
      setPasswordsFor(null);
      setPw({ auth: "", priv: "" });
    }
  };
  const addTrap = async () => {
    if (await act(() => setSnmpTrap(site, newTrap.name, { address: newTrap.address, reader: newTrap.reader || readers[0] }),
      `Trap target ${newTrap.name} saved: switches send to it from their next deploy.`)) {
      setNewTrap({ name: "", address: "", reader: "" });
    }
  };
  const typePasswords = async () => {
    setError(null);
    try {
      setJobId((await requestPasswordJob(site, typeable)).id);
    } catch (e) {
      setError(clean(e));
    }
  };
  const approve = async () => {
    if (jobId === null) return;
    try {
      await approvePasswordJob(jobId);
      setJob(await fetchPasswordJob(jobId));
    } catch (e) {
      setError(clean(e));
    }
  };

  const v2cBlocked = a.v2c ? v2cOffBlocked(a) : null;
  const pwProblem = pw.auth && passwordProblem(pw.auth) || pw.priv && passwordProblem(pw.priv)
    || (pw.auth && pw.auth === pw.priv ? "Use two different passwords." : null);

  return (
    <div className="routing" data-tour="snmp-access">
      <p>
        Read with the platform's community (v2c):{" "}
        <span className="toggle" role="group" aria-label="v2c">
          <button aria-pressed={a.v2c} disabled={!canOperate || busy || a.v2c}
                  onClick={() => act(() => setSnmpV2c(site, true), "v2c on: switches answer the community at their next deploy.")}>On</button>
          <button aria-pressed={!a.v2c} disabled={!canOperate || busy || !a.v2c || !!v2cBlocked} title={v2cBlocked ?? undefined}
                  onClick={() => act(() => setSnmpV2c(site, false), "v2c off: only v3 readers can read from the next deploy.")}>Off</button>
        </span>
        {v2cBlocked && <span className="faint small"> {v2cBlocked}</span>}
      </p>

      <h3>Readers (SNMPv3, read only)</h3>
      {readers.length === 0 ? <p className="muted small">None yet. A reader is a login your monitoring system uses, with its own two passwords.</p> : (
        <table className="dense" data-tour="snmp-readers">
          <thead><tr><th>Name</th><th>Security</th><th>Passwords</th><th /></tr></thead>
          <tbody>
            {readers.map((r) => {
              const blocked = removeBlocked(a, r);
              return (
                <tr key={r}>
                  <td className="mono">{r}</td>
                  <td>{a.readers[r].auth.toUpperCase()} + {a.readers[r].priv.toUpperCase()}</td>
                  <td>{stored.has(r) ? "stored" : <span className="warn">not set yet</span>}</td>
                  <td>
                    {canOperate && <button onClick={() => { setPasswordsFor(r); setPw({ auth: "", priv: "" }); }}>{stored.has(r) ? "Change passwords" : "Set passwords"}</button>}
                    {canOperate && removing !== r && <button disabled={!!blocked} title={blocked ?? undefined} onClick={() => setRemoving(r)}>Remove</button>}
                    {removing === r && (
                      <span className="small"> Your monitoring system loses this login at the next deploy.{" "}
                        <button className="danger" onClick={() => { setRemoving(null); act(() => deleteSnmpReader(site, r), `Reader ${r} removed at the next deploy.`); }}>Remove {r}</button>
                        <button onClick={() => setRemoving(null)}>Keep</button>
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {readers.length > 0 && (
        <p className="small">
          <button onClick={readSwitches} disabled={reading} title="Asks every active switch now; a password itself cannot be read back.">
            {reading ? "Reading…" : "Read from the switches"}
          </button>
        </p>
      )}
      {onSwitches && (
        <table className="dense">
          <thead><tr><th>Switch</th>{readers.map((r) => <th key={r} className="mono">{r}</th>)}</tr></thead>
          <tbody>
            {onSwitches.map((s) => (
              <tr key={s.device}>
                <td className="mono">{s.device}</td>
                {s.error ? <td colSpan={readers.length} className="warn">{s.error}</td>
                  : readers.map((r) => {
                    const state = s.readers?.[r] ?? "missing";
                    return <td key={r} className={state === "ok" ? undefined : "warn"}>{READER_STATE[state]}</td>;
                  })}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {passwordsFor && (
        <div className="form-row">
          <label htmlFor="snmp-auth">Authentication password for {passwordsFor}
            <input id="snmp-auth" type="password" autoComplete="new-password" value={pw.auth} onChange={(e) => setPw({ ...pw, auth: e.target.value })} />
          </label>
          <label htmlFor="snmp-priv">Encryption password
            <input id="snmp-priv" type="password" autoComplete="new-password" value={pw.priv} onChange={(e) => setPw({ ...pw, priv: e.target.value })} />
          </label>
          <button className="primary" disabled={!pw.auth || !pw.priv || !!pwProblem || busy} onClick={() => savePasswords(passwordsFor)}>Store</button>
          <button onClick={() => setPasswordsFor(null)}>Cancel</button>
          {pwProblem && <p className="error small">{pwProblem}</p>}
          <p className="faint small">Stored by the platform and never shown again. Give the same two to your monitoring system.</p>
        </div>
      )}
      {canOperate && (
        <div className="form-row">
          <label htmlFor="snmp-reader">New reader
            <input id="snmp-reader" value={newReader.name} onChange={(e) => setNewReader({ ...newReader, name: e.target.value })} placeholder="e.g. nms" />
          </label>
          {expert && a.algorithms && (
            <>
              <label htmlFor="snmp-alg-auth">Authentication
                <select id="snmp-alg-auth" value={newReader.auth} onChange={(e) => setNewReader({ ...newReader, auth: e.target.value })}>
                  {a.algorithms.auth.map((x) => <option key={x}>{x}</option>)}
                </select>
              </label>
              <label htmlFor="snmp-alg-priv">Encryption
                <select id="snmp-alg-priv" value={newReader.priv} onChange={(e) => setNewReader({ ...newReader, priv: e.target.value })}>
                  {a.algorithms.priv.map((x) => <option key={x}>{x}</option>)}
                </select>
              </label>
            </>
          )}
          <button disabled={!NAME.test(newReader.name) || busy} title={newReader.name && !NAME.test(newReader.name) ? "Letters, digits, - or _, starting with a letter" : undefined}
                  onClick={addReader}>Add reader</button>
        </div>
      )}

      <h3>Trap targets</h3>
      {Object.keys(a.traps).length === 0 ? <p className="muted small">None: the switches send no SNMP traps.</p> : (
        <table className="dense" data-tour="snmp-traps">
          <thead><tr><th>Name</th><th>Address</th><th>Sent as</th><th /></tr></thead>
          <tbody>
            {Object.entries(a.traps).sort(([x], [y]) => x.localeCompare(y)).map(([n, t]) => (
              <tr key={n}>
                <td className="mono">{n}</td><td className="mono">{t.address}</td><td className="mono">{t.reader}</td>
                <td>{canOperate && <button disabled={busy} onClick={() => act(() => deleteSnmpTrap(site, n), `Trap target ${n} removed at the next deploy.`)}>Remove</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {canOperate && (
        <div className="form-row">
          <label htmlFor="trap-name">Name
            <input id="trap-name" value={newTrap.name} onChange={(e) => setNewTrap({ ...newTrap, name: e.target.value })} placeholder="e.g. nms-1" />
          </label>
          <label htmlFor="trap-address">Address
            <input id="trap-address" value={newTrap.address} onChange={(e) => setNewTrap({ ...newTrap, address: e.target.value })} placeholder="IPv4, reached over MEth" />
          </label>
          <label htmlFor="trap-reader">Sent as
            <select id="trap-reader" value={newTrap.reader || readers[0] || ""} onChange={(e) => setNewTrap({ ...newTrap, reader: e.target.value })} disabled={!readers.length}>
              {readers.map((r) => <option key={r}>{r}</option>)}
            </select>
          </label>
          <button disabled={!readers.length || !NAME.test(newTrap.name) || !newTrap.address.trim() || busy}
                  title={readers.length ? undefined : "Add a reader first: traps are sent as one"} onClick={addTrap}>Add trap target</button>
        </div>
      )}

      <h3>Passwords on the switches</h3>
      <p className="small">
        After a deploy has created the readers, type their stored passwords on every active switch: one switch at a time,
        approved like a deploy, each checked afterwards.{" "}
        {canOperate && (
          <button className="primary" disabled={!typeable.length || (job !== null && RUNNING.includes(job.status))}
                  title={typeable.length ? undefined : "Store a reader's passwords first"} onClick={typePasswords}>
            Type passwords of {typeable.length ? typeable.join(", ") : "…"}
          </button>
        )}
      </p>
      {job && (
        <div className="small" aria-live="polite">
          Password job #{job.id}: <strong>{job.status}</strong>
          {job.steps.map((s) => <span key={s.device} className="mono"> · {s.device} {s.status}</span>)}
          {job.error && <p className="error">{job.error}</p>}
          {job.status === "diffed" && (canApprove
            ? <> <button className="primary" onClick={approve}>Approve and type on {job.steps.length} switch{job.steps.length === 1 ? "" : "es"}</button></>
            : <span className="muted"> Waiting for someone who may approve deploys of {site}.</span>)}
          {expert && job.steps[0]?.patch && <pre className="config">{job.steps[0].patch}</pre>}
        </div>
      )}

      {canOperate && others.length > 0 && (
        <p className="small">
          <label htmlFor="snmp-copy">Copy readers and trap targets from{" "}
            <select id="snmp-copy" value={copyFrom} onChange={(e) => setCopyFrom(e.target.value)}>
              <option value="">another fabric…</option>
              {others.map((f) => <option key={f.site} value={f.site}>{f.name}</option>)}
            </select>
          </label>{" "}
          <button disabled={!copyFrom || busy} onClick={() => act(() => copySnmpAccess(site, copyFrom), `Readers and trap targets copied from ${copyFrom}, passwords included.`)}>Copy</button>
        </p>
      )}
      {error && <p className="error">{error}</p>}
      <button className="linklike small" aria-expanded={expert} onClick={() => setExpert(!expert)}>{expert ? "Hide Expert view" : "Expert view"}</button>
      {expert && <pre className="config">{switchLines(a).join("\n")}</pre>}
    </div>
  );
}
