import { useState } from "react";
import { changeMyPassword, changeUser, createUser, deleteUser, fetchFabrics, fetchUsers, type Account, type FabricLimits, type Role } from "./api";
import { useCan } from "./role";
import { useFetch } from "./useFetch";

// Accounts: admins only. The API refuses anyone else and keeps at least
// one admin; every change is in the audit trail.

const ROLES: { role: Role; what: string }[] = [
  { role: "viewer", what: "sees, changes nothing" },
  { role: "operator", what: "changes intent, runs checks, asks for deploys; may be limited to fabrics" },
  { role: "approver", what: "also approves deploys" },
  { role: "admin", what: "also accounts, settings, deleting a fabric" },
];
const SOURCE: Record<Account["source"], string> = { local: "local", ldap: "Active Directory", radius: "RADIUS" };
const clean = (e: unknown) => (e instanceof Error ? e.message : String(e)).replace(/^\d+: /, "");

export function Users({ me }: { me: string }) {
  const isAdmin = useCan("admin");
  const users = useFetch(() => (isAdmin ? fetchUsers() : Promise.resolve([] as Account[])), [isAdmin]);
  const [form, setForm] = useState({ username: "", password: "", role: "viewer" as Role });
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resetting, setResetting] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  const [limiting, setLimiting] = useState<string | null>(null);

  if (!isAdmin) return <section><p className="muted">Accounts are managed by admins.</p></section>;

  const act = async (what: () => Promise<unknown>, text: string) => {
    setError(null);
    setNotice(null);
    try {
      await what();
      setNotice(text);
      users.reload();
    } catch (e) {
      setError(clean(e));
    }
  };

  return (
    <section className="users-page">
      <div className="toolbar"><h1>Accounts</h1></div>
      {error && <p className="error">{error}</p>}
      {notice && <p className="notice">{notice}</p>}
      <div className="panel" data-tour="users-new">
        <header><h2>New account</h2><span className="hint">at least 8 characters for the password</span></header>
        <form className="form-row" onSubmit={(e) => {
          e.preventDefault();
          act(() => createUser(form), `${form.username.trim()} created as ${form.role}.`).then(() => setForm({ username: "", password: "", role: "viewer" }));
        }}>
          <label>Username<input id="new-username" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} autoComplete="off" /></label>
          <label>Password<input id="new-password" type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} autoComplete="new-password" /></label>
          <div className="role-pick" role="radiogroup" aria-label="Role">
            {ROLES.map((r) => (
              <button type="button" key={r.role} className="role-card" aria-pressed={form.role === r.role} onClick={() => setForm({ ...form, role: r.role })}>
                <b>{r.role}</b><span>{r.what}</span>
              </button>
            ))}
          </div>
          <button type="submit" className="primary" disabled={!form.username.trim() || form.password.length < 8}>Create</button>
        </form>
      </div>
      <div className="panel" data-tour="users-list">
        <header><h2>Accounts</h2><span className="hint">{users.data?.length ?? 0}</span></header>
        <div className="tablewrap">
          <table className="devices">
            <thead><tr><th>User</th><th>From</th><th>Role</th><th>Fabrics</th><th>Since</th><th /></tr></thead>
            <tbody>
              {(users.data ?? []).map((u) => [
                <tr key={u.username}>
                  <td><b>{u.username}</b>{u.username === me && <span className="pill">you</span>}
                    {!u.active && <span className="pill warnpill">no access</span>}</td>
                  <td className="small">{SOURCE[u.source]}</td>
                  <td>
                    {u.source !== "local" ? <span title="set by the directory at each sign-in">{u.role}</span> : <select value={u.role} aria-label={`Role of ${u.username}`}
                      onChange={(e) => act(() => changeUser(u.username, { role: e.target.value as Role }), `${u.username} is now ${e.target.value}.`)}>
                      {ROLES.map((r) => <option key={r.role} value={r.role}>{r.role}</option>)}
                    </select>}
                  </td>
                  <td>
                    {u.source !== "local" ? <span className="small">{limitText(u.fabrics)}</span>
                      : u.role === "viewer" || u.role === "operator" ? (
                      <button className="linklike small" onClick={() => setLimiting(limiting === u.username ? null : u.username)}>
                        {limitText(u.fabrics)}
                      </button>
                    ) : <span className="faint small">every fabric</span>}
                  </td>
                  <td className="small muted">{new Date(u.created_at).toLocaleDateString()}</td>
                  <td className="nowrap">
                    {resetting === u.username ? (
                      <span className="inline">
                        <input type="password" placeholder="new password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
                        <button className="small primary" disabled={password.length < 8}
                          onClick={() => act(() => changeUser(u.username, { password }), `New password set for ${u.username}; they are signed out.`).then(() => { setResetting(null); setPassword(""); })}>Set</button>
                        <button className="small" onClick={() => { setResetting(null); setPassword(""); }}>Cancel</button>
                      </span>
                    ) : deleting === u.username ? (
                      <span className="inline">
                        <span className="small">Delete {u.username}?</span>
                        <button className="small danger" onClick={() => act(() => deleteUser(u.username), `${u.username} deleted.`).then(() => setDeleting(null))}>Delete</button>
                        <button className="small" onClick={() => setDeleting(null)}>Cancel</button>
                      </span>
                    ) : (
                      <span className="inline">
                        {u.source === "local" && <button className="linklike small" onClick={() => setResetting(u.username)}>Reset password</button>}
                        {u.username !== me && <button className="linklike small" onClick={() => setDeleting(u.username)}>Delete</button>}
                      </span>
                    )}
                  </td>
                </tr>,
                limiting === u.username && (
                  <tr key={`${u.username}-fabrics`} className="limits-row">
                    <td colSpan={6}>
                      <FabricLimitsEditor account={u} onCancel={() => setLimiting(null)}
                        onSave={(fabrics) => act(() => changeUser(u.username, { fabrics }), `${u.username}: ${limitText(fabrics)}.`).then(() => setLimiting(null))} />
                    </td>
                  </tr>
                ),
              ])}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

const limitText = (fabrics: FabricLimits) =>
  fabrics === null ? "every fabric"
    : Object.entries(fabrics).map(([site, approve]) => `${site}${approve ? " (approves)" : ""}`).join(", ");

/** Which fabrics a viewer or operator reaches; an operator may approve deploys of some. */
function FabricLimitsEditor({ account, onSave, onCancel }: {
  account: Account; onSave: (fabrics: FabricLimits) => void; onCancel: () => void;
}) {
  const fabrics = useFetch(fetchFabrics, []);
  const [every, setEvery] = useState(account.fabrics === null);
  const [chosen, setChosen] = useState<Record<string, boolean>>(account.fabrics ?? {});
  const operator = account.role === "operator";
  const toggle = (site: string) => {
    const next = { ...chosen };
    if (site in next) delete next[site];
    else next[site] = false;
    setChosen(next);
  };
  return (
    <div className="limits-editor">
      <div className="role-pick" role="radiogroup" aria-label={`Fabrics of ${account.username}`}>
        <button type="button" className="role-card" aria-pressed={every} onClick={() => setEvery(true)}>
          <b>Every fabric</b><span>sees and works in every fabric, now and future ones</span>
        </button>
        <button type="button" className="role-card" aria-pressed={!every} onClick={() => setEvery(false)}>
          <b>Only these fabrics</b><span>sees nothing of the others; cannot create or import a fabric</span>
        </button>
      </div>
      {!every && (
        <ul className="limits-list">
          {(fabrics.data ?? []).map((f) => (
            <li key={f.site}>
              <label><input type="checkbox" checked={f.site in chosen} onChange={() => toggle(f.site)} /> {f.name}</label>
              {operator && f.site in chosen && (
                <label className="small"><input type="checkbox" checked={chosen[f.site]}
                  onChange={(e) => setChosen({ ...chosen, [f.site]: e.target.checked })} /> may approve its deploys</label>
              )}
            </li>
          ))}
        </ul>
      )}
      <span className="inline">
        <button className="small primary" disabled={!every && !Object.keys(chosen).length}
          onClick={() => onSave(every ? null : chosen)}>Save</button>
        <button className="small" onClick={onCancel}>Cancel</button>
        {!operator && !every && <span className="muted small">Viewers never approve; make the account an operator for that.</span>}
      </span>
    </div>
  );
}

/** "Change my password" for every user, from the top bar. */
export function MyPassword({ onDone }: { onDone: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState<string | null>(null);
  const problem = next.length < 8 ? "at least 8 characters" : next !== again ? "the two new passwords differ" : null;
  return (
    <form className="my-password" onSubmit={async (e) => {
      e.preventDefault();
      setError(null);
      try {
        await changeMyPassword(current, next);
        onDone();   // signed out everywhere: back to the login page
      } catch (err) {
        setError(clean(err));
      }
    }}>
      <b>Change my password</b>
      <input type="password" placeholder="current password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" />
      <input type="password" placeholder="new password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" />
      <input type="password" placeholder="new password again" value={again} onChange={(e) => setAgain(e.target.value)} autoComplete="new-password" />
      {(error ?? (next && problem)) && <span className={error ? "error small" : "muted small"}>{error ?? problem}</span>}
      <button type="submit" className="primary" disabled={!current || problem !== null}>Change it</button>
      <span className="muted small">You will sign in again with the new password.</span>
    </form>
  );
}
