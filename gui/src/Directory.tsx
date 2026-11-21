import { useState } from "react";
import {
  fetchDirectory, fetchFabrics, resetSetting, saveSetting, testDirectory,
  type DirectoryTestResult, type GroupMapping, type Role, type SettingRow,
} from "./api";
import { useCan } from "./role";
import { SettingField } from "./Settings";
import { useFetch } from "./useFetch";

// Directory sign-in: Active Directory over LDAPS, then RADIUS. Local
// accounts always sign in locally. People get the role and fabrics their groups (or RADIUS
// attribute) map to, at every sign-in; no mapping, no access.

const clean = (e: unknown) => (e instanceof Error ? e.message : String(e)).replace(/^\d+: /, "");
const ROLES: Role[] = ["viewer", "operator", "approver", "admin"];
const LDAP_KEYS = ["DIRECTORY_LDAP_URLS", "DIRECTORY_LDAP_BIND_DN", "DIRECTORY_LDAP_BIND_PASSWORD", "DIRECTORY_LDAP_BASE_DN",
  "DIRECTORY_LDAP_CA_CERT", "DIRECTORY_LDAP_USER_FILTER", "DIRECTORY_LDAP_GROUP_FILTER"];
const RADIUS_KEYS = ["DIRECTORY_RADIUS_SERVERS", "DIRECTORY_RADIUS_SECRET", "DIRECTORY_RADIUS_ATTRIBUTE"];
const OUTCOME: Record<DirectoryTestResult["outcome"], string> = {
  granted: "would be signed in", unknown: "is not known to any directory", refused: "would be refused",
  "no-access": "is known but has no mapped group or attribute: no access", unavailable: "could not be checked: the directory did not answer",
};

export function Directory() {
  const isAdmin = useCan("admin");
  const data = useFetch(() => (isAdmin ? fetchDirectory() : Promise.resolve(null)), [isAdmin]);
  const [notice, setNotice] = useState<string | null>(null);
  if (!isAdmin) return <section><p className="muted">The directory is managed by admins.</p></section>;
  if (data.error) return <p className="error">{data.error}</p>;
  if (!data.data) return <p className="muted">Loading…</p>;
  const rows = Object.fromEntries(data.data.settings.map((r) => [r.key, r]));
  const withDefault = (r: SettingRow): SettingRow =>
    r.value || !data.data!.defaults[r.key] ? r : { ...r, help: `${r.help} Now: the default.`.trim() };
  const changed = (text: string) => { setNotice(text); data.reload(); };
  const on = data.data.providers;

  return (
    <section className="settings-page">
      <div className="toolbar"><h1>Directory sign-in</h1></div>
      <p className="muted">
        People sign in with their Active Directory account, or through RADIUS when AD does not know them. Their role and
        fabrics come from their groups at every sign-in. Local accounts always sign in locally, whatever the directory's state.
      </p>
      {notice && <p className="notice">{notice}</p>}
      <div className="panel" data-tour="directory-ad">
        <header><h2>Active Directory</h2><span className={on.includes("ldap") ? "pill good" : "pill"}>{on.includes("ldap") ? "in use" : "not complete"}</span></header>
        <div className="settings-rows">
          {LDAP_KEYS.map((k) => rows[k] && <SettingField key={k} row={withDefault(rows[k]!)} onChanged={changed} />)}
        </div>
      </div>
      <GroupMappingPanel row={rows["DIRECTORY_LDAP_GROUPS"]} onChanged={changed} />
      <div className="panel" data-tour="directory-radius">
        <header><h2>RADIUS</h2><span className={on.includes("radius") ? "pill good" : "pill"}>{on.includes("radius") ? "in use" : "not complete"}</span></header>
        <p className="empty muted small">
          The attribute's value gives the role and fabrics: <code>operator:example+,east</code> is an operator on example and east who
          may approve example's deploys; a role alone reaches every fabric.
        </p>
        <div className="settings-rows">
          {RADIUS_KEYS.map((k) => rows[k] && <SettingField key={k} row={withDefault(rows[k]!)} onChanged={changed} />)}
        </div>
      </div>
      <TestSignIn />
    </section>
  );
}

function GroupMappingPanel({ row, onChanged }: { row?: SettingRow; onChanged: (text: string) => void }) {
  const fabrics = useFetch(fetchFabrics, []);
  const saved: GroupMapping[] = row?.value ? JSON.parse(row.value) : [];
  const [rows, setRows] = useState<GroupMapping[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const editing = rows !== null;
  const shown = rows ?? saved;
  const update = (i: number, change: Partial<GroupMapping>) =>
    setRows(shown.map((r, j) => (j === i ? { ...r, ...change } : r)));
  const save = async () => {
    setError(null);
    try {
      if (shown.length) await saveSetting("DIRECTORY_LDAP_GROUPS", JSON.stringify(shown));
      else await resetSetting("DIRECTORY_LDAP_GROUPS");
      setRows(null);
      onChanged(`Group mapping saved: ${shown.length} group${shown.length === 1 ? "" : "s"}.`);
    } catch (e) {
      setError(clean(e));
    }
  };

  return (
    <div className="panel" data-tour="directory-groups">
      <header>
        <h2>Groups and what they give</h2>
        <span className="hint">several groups: the highest role, fabrics added up</span>
        {!editing && <button className="small" onClick={() => setRows(saved)}>Edit</button>}
      </header>
      {shown.length === 0 && !editing && <p className="empty muted">No group mapped yet: nobody can sign in through AD.</p>}
      {shown.length > 0 && (
        <div className="tablewrap">
          <table className="devices">
            <thead><tr><th>AD group (DN)</th><th>Role</th><th>Fabrics</th>{editing && <th />}</tr></thead>
            <tbody>
              {shown.map((r, i) => (
                <tr key={i}>
                  <td>{editing ? <input className="mono wide" value={r.group} placeholder="CN=NetOps,OU=Groups,DC=example,DC=net"
                    onChange={(e) => update(i, { group: e.target.value })} aria-label="Group DN" /> : <span className="mono small">{r.group}</span>}</td>
                  <td>{editing ? (
                    <select value={r.role} onChange={(e) => update(i, { role: e.target.value as Role })} aria-label="Role">
                      {ROLES.map((x) => <option key={x}>{x}</option>)}
                    </select>
                  ) : r.role}</td>
                  <td>
                    {r.role === "approver" || r.role === "admin" ? <span className="faint small">every fabric</span>
                      : editing ? (
                        <span className="inline wrap">
                          <label className="small"><input type="checkbox" checked={r.fabrics === null}
                            onChange={(e) => update(i, { fabrics: e.target.checked ? null : {} })} /> every fabric</label>
                          {r.fabrics !== null && (fabrics.data ?? []).map((f) => (
                            <span key={f.site} className="small">
                              <label><input type="checkbox" checked={f.site in r.fabrics!} onChange={() => {
                                const next = { ...r.fabrics! };
                                if (f.site in next) delete next[f.site]; else next[f.site] = false;
                                update(i, { fabrics: next });
                              }} /> {f.site}</label>
                              {r.role === "operator" && f.site in r.fabrics! && (
                                <label className="faint"> <input type="checkbox" checked={r.fabrics![f.site]}
                                  onChange={(e) => update(i, { fabrics: { ...r.fabrics!, [f.site]: e.target.checked } })} /> approves</label>
                              )}
                            </span>
                          ))}
                        </span>
                      ) : r.fabrics === null ? "every fabric"
                        : Object.entries(r.fabrics).map(([s, a]) => `${s}${a ? " (approves)" : ""}`).join(", ")}
                  </td>
                  {editing && <td><button className="linklike small" onClick={() => setRows(shown.filter((_, j) => j !== i))}>Remove</button></td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {editing && (
        <p className="empty">
          <button className="small" onClick={() => setRows([...shown, { group: "", role: "viewer", fabrics: null }])}>Add a group</button>{" "}
          <button className="small primary" onClick={save}>Save</button>{" "}
          <button className="small" onClick={() => { setRows(null); setError(null); }}>Cancel</button>
        </p>
      )}
      {error && <p className="error empty">{error}</p>}
    </div>
  );
}

function TestSignIn() {
  const [form, setForm] = useState({ username: "", password: "" });
  const [result, setResult] = useState<DirectoryTestResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await testDirectory(form.username, form.password));
      setForm({ ...form, password: "" });
    } catch (e) {
      setError(clean(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="panel" data-tour="directory-test">
      <header><h2>Test a sign-in</h2><span className="hint">nobody is signed in and no account is kept</span></header>
      <form className="form-row empty" onSubmit={(e) => { e.preventDefault(); run(); }}>
        <label>Username<input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} autoComplete="off" /></label>
        <label>Password<input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} autoComplete="off" /></label>
        <button type="submit" className="primary" disabled={busy || !form.username || !form.password}>{busy ? "Asking…" : "Test"}</button>
      </form>
      {error && <p className="error empty">{error}</p>}
      {result && (
        <div className="empty">
          <p className={result.outcome === "granted" ? "notice" : "error"}>
            {form.username || "This user"} {OUTCOME[result.outcome]}
            {result.outcome === "granted" && <> as <b>{result.role}</b> on {result.fabrics === null ? "every fabric"
              : Object.entries(result.fabrics).map(([s, a]) => `${s}${a ? " (approves)" : ""}`).join(", ")} ({result.source === "ldap" ? "Active Directory" : "RADIUS"})</>}
            {result.detail && result.outcome !== "granted" && <span className="muted"> — {result.detail}</span>}
          </p>
          {result.groups.length > 0 && (
            <>
              <p className="muted small">Groups AD returned (copy one into the mapping above):</p>
              <ul className="mono small">{result.groups.map((g) => <li key={g}>{g}</li>)}</ul>
            </>
          )}
        </div>
      )}
    </div>
  );
}
