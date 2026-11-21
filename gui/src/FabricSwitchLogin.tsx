import { useState } from "react";
import { addSwitchAccount, fetchSwitchLogin, removeSwitchAccount, setSwitchLogin } from "./api";
import { useCan } from "./role";
import { useFetch } from "./useFetch";

// Switch login: people log in as name@domain over RADIUS. Off by default.
// The platform's own accounts stay in the default domain; each switch tests the change under a
// trial commit. The shared key is write-only.

const clean = (e: unknown) => (e instanceof Error ? e.message : String(e)).replace(/^\d+: /, "");

export function FabricSwitchLogin({ site, onSaved }: { site: string; onSaved: (text: string) => void }) {
  const canOperate = useCan("operator");
  const login = useFetch(() => fetchSwitchLogin(site), [site]);
  const [servers, setServers] = useState<string | null>(null);
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  const [level, setLevel] = useState(1);
  const [password, setPassword] = useState("");

  const l = login.data;
  if (login.error) return <p className="error">{login.error}</p>;
  if (!l) return <p className="muted">Loading…</p>;
  const list = (servers ?? l.servers.join(", ")).split(/[\s,]+/).filter(Boolean);

  const save = async (enabled: boolean) => {
    setError(null);
    setBusy(true);
    try {
      await setSwitchLogin(site, { enabled, domain: l.domain, servers: list, ...(key ? { key } : {}) });
      setKey("");
      setServers(null);
      login.reload();
      onSaved(`Switch login ${enabled ? "saved" : "turned off"}: deploy it to reach the switches`);
    } catch (e) {
      setError(clean(e));
    } finally {
      setBusy(false);
    }
  };

  const account = async (call: () => Promise<unknown>, text: string) => {
    setError(null);
    setBusy(true);
    try {
      await call();
      setName("");
      setPassword("");
      login.reload();
      onSaved(`${text}: deploy it to reach the switches`);
    } catch (e) {
      setError(clean(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <p>{l.enabled
        ? <>On: people log in as <code>name@{l.domain}</code>, checked by {l.servers.join(" and ")}.</>
        : "Off: people log in with accounts stored on each switch."}</p>
      <p className="muted">
        The RADIUS answer sets what someone may do (Huawei-Exec-Privilege); with none they can only look. The platform's own
        accounts are not affected. Each switch tests the change and takes it back by itself if the platform cannot log in again.
      </p>
      {canOperate && (
        <div className="form-row">
          <label>RADIUS servers
            <input value={servers ?? l.servers.join(", ")} placeholder="10.0.0.5, 10.0.0.6" onChange={(e) => setServers(e.target.value)} />
          </label>
          {!l.enabled && (
            <label>Shared key {l.key_set && <span className="muted">(set; leave empty to keep)</span>}
              <input type="password" autoComplete="new-password" value={key} onChange={(e) => setKey(e.target.value)} />
            </label>
          )}
          <button type="button" disabled={busy || !list.length || (!l.key_set && !key)} onClick={() => save(true)}>
            {l.enabled ? "Save" : "Turn on"}
          </button>
          {l.enabled && <button type="button" disabled={busy} onClick={() => save(false)}>Turn off</button>}
        </div>
      )}
      <h3>Local accounts</h3>
      <p className="muted">
        For when RADIUS cannot be reached. Accounts made here go on every active switch; the person changes the password at first
        login. Accounts already on a switch are left alone. To reset a password, delete the account, deploy, and add it again.
      </p>
      {l.accounts.length > 0 && (
        <table className="dense">
          <tbody>
            {l.accounts.map((a) => (
              <tr key={a.name}>
                <td><code>{a.name}</code></td>
                <td>{a.level === 3 ? "Can change config" : "Can look"}</td>
                {canOperate && <td><button type="button" disabled={busy} onClick={() => account(() => removeSwitchAccount(site, a.name), `${a.name} removed`)}>Delete</button></td>}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {canOperate && (
        <div className="form-row">
          <label>Name <input value={name} placeholder="six letters at least" onChange={(e) => setName(e.target.value)} /></label>
          <label>May
            <select value={level} onChange={(e) => setLevel(Number(e.target.value))}>
              <option value={1}>Look</option>
              <option value={3}>Change config</option>
            </select>
          </label>
          <label>First password <input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>
          <button type="button" disabled={busy || name.length < 6 || password.length < 8}
            onClick={() => account(() => addSwitchAccount(site, name, level, password), `${name} added`)}>Add account</button>
        </div>
      )}
      {error && <p className="error">{error}</p>}
    </>
  );
}
