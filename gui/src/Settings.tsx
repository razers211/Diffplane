import { useState } from "react";
import { fetchAlertReceiver, fetchSettings, resetSetting, saveSetting, sendTestAlert, type SettingRow, type SettingsView } from "./api";
import { useCan } from "./role";
import { useFetch } from "./useFetch";

// Platform settings: admins only. A value set here replaces the .env one
// and applies at once (or at the next start, where it says so). Secrets are never shown: only
// where they come from, and a field to replace them.

const clean = (e: unknown) => (e instanceof Error ? e.message : String(e)).replace(/^\d+: /, "");
const SOURCE: Record<SettingRow["source"], [string, string]> = {
  gui: ["set here", "pill good"], env: ["from .env", "pill"], unset: ["not set", "pill warnpill"],
};

export function Settings() {
  const isAdmin = useCan("admin");
  const data = useFetch(() => (isAdmin ? fetchSettings() : Promise.resolve(null)), [isAdmin]);
  const [notice, setNotice] = useState<string | null>(null);
  if (!isAdmin) return <section><p className="muted">Settings are managed by admins.</p></section>;
  if (data.error) return <p className="error">{data.error}</p>;
  if (!data.data) return <p className="muted">Loading…</p>;
  const groups = [...new Set(data.data.settings.map((s) => s.group))];

  return (
    <section className="settings-page">
      <div className="toolbar"><h1>Settings</h1></div>
      <p className="muted">
        A value set here replaces the one in <code>deploy/.env</code> and applies at once, unless it says otherwise.
        Passwords and keys are never shown again once set: replace them, or go back to the <code>.env</code> value.
      </p>
      {notice && <p className="notice">{notice}</p>}
      {groups.map((g) => (
        <div key={g} className="panel" data-tour={`settings-${g.toLowerCase().replace(/[^a-z]+/g, "-")}`}>
          <header><h2>{g}</h2></header>
          <div className="settings-rows">
            {data.data!.settings.filter((s) => s.group === g).map((s) => (
              <SettingField key={s.key} row={s} onChanged={(text) => { setNotice(text); data.reload(); }} />
            ))}
          </div>
          {g === "Alerts" && <AlertTest version={data.data!} />}
        </div>
      ))}
      <div className="panel" data-tour="settings-fixed">
        <header><h2>Set in .env only</h2><span className="hint">changed in deploy/.env, applied by a restart</span></header>
        <p className="empty">
          These connect the platform to its own databases and decide where it listens: a wrong value here would lock it out,
          so they are not changed from a web page.
        </p>
        <dl className="settings">
          {data.data.fixed.map((f) => (
            <div key={f.key} className="fixed-row">
              <dt className="mono">{f.key}</dt>
              <dd>{f.value !== null ? <span className="mono">{f.value || "—"}</span> : <span className={f.set ? "pill" : "pill warnpill"}>{f.set ? "set" : "not set"}</span>}</dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
}

export function SettingField({ row, onChanged }: { row: SettingRow; onChanged: (text: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [label, cls] = SOURCE[row.source];

  const act = async (what: () => Promise<SettingsView>, text: string) => {
    setBusy(true);
    setError(null);
    try {
      const result = await what();
      setEditing(false);
      setValue("");
      onChanged(result.applied ? `${text} ${result.applied}.` : text);
    } catch (e) {
      setError(clean(e));
    } finally {
      setBusy(false);
    }
  };
  const open = () => { setValue(row.secret ? "" : row.value || (row.choices[0] ?? "")); setEditing(true); setError(null); };

  return (
    <div className="setting-row">
      <div className="what">
        <b>{row.label}</b>
        {row.help && <span className="muted small">{row.help}</span>}
      </div>
      <div className="now">
        {row.secret ? <span className="mono faint">••••••</span>
          : row.kind === "pem" ? <span className="mono small">{row.value ? `${(row.value.match(/BEGIN CERTIFICATE/g) ?? []).length} certificate(s)` : <span className="faint">empty</span>}</span>
          : <span className="mono">{row.value || <span className="faint">empty</span>}</span>}
        <span className={cls}>{label}</span>
        {row.applies !== "now" && <span className="pill warnpill">applies at the {row.applies}</span>}
      </div>
      <div className="act">
        {editing ? (
          <form className="inline" onSubmit={(e) => { e.preventDefault(); act(() => saveSetting(row.key, value), `${row.label}: saved${row.applies === "now" ? " and in use" : `, used from the ${row.applies}`}.`); }}>
            {row.kind === "choice" ? (
              <select autoFocus value={value} onChange={(e) => setValue(e.target.value)} aria-label={row.label}>
                {row.choices.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            ) : row.kind === "pem" ? (
              <textarea className="mono pem" rows={8} autoFocus value={value} onChange={(e) => setValue(e.target.value)}
                placeholder="-----BEGIN CERTIFICATE-----" aria-label={row.label} />
            ) : (
              <input className="mono" type={row.secret ? "password" : "text"} autoFocus value={value} onChange={(e) => setValue(e.target.value)}
                placeholder={row.secret ? "new value" : ""} autoComplete="off" aria-label={row.label} />
            )}
            <button type="submit" className="small primary" disabled={busy}>Save</button>
            <button type="button" className="small" onClick={() => setEditing(false)}>Cancel</button>
          </form>
        ) : (
          <span className="inline">
            <button className="small" onClick={open}>{row.secret ? (row.source === "unset" ? "Set" : "Replace") : "Change"}</button>
            {row.source === "gui" && (
              <button className="linklike small" onClick={() => act(() => resetSetting(row.key), `${row.label}: back to the .env value.`)}>Back to .env</button>
            )}
          </span>
        )}
        {error && <span className="error small">{error}</span>}
      </div>
    </div>
  );
}

/** Where alerts go now, and a test alert through Alertmanager. */
function AlertTest({ version }: { version: SettingsView }) {
  const now = useFetch(fetchAlertReceiver, [version]);
  const [result, setResult] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const test = async () => {
    setBusy(true);
    try {
      const r = await sendTestAlert();
      setResult(r.receiver === "none" ? "Sent to Alertmanager; with no receiver it is only listed there." : `Sent; it reaches the ${r.receiver} receiver in about 30 s.`);
    } catch (e) {
      setResult(clean(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="setting-row">
      <div className="what">
        <b>Alerts go to</b>
        {now.data?.problem && <span className="error small">{now.data.problem}: alerts are held until it is complete.</span>}
      </div>
      <div className="now">{now.data && <span className={now.data.receiver === "none" ? "pill" : "pill good"}>{now.data.receiver}</span>}</div>
      <div className="act">
        <button className="small" onClick={test} disabled={busy}>Send a test alert</button>
        {result && <span className="muted small">{result}</span>}
      </div>
    </div>
  );
}
