import { useState, type FormEvent } from "react";
import { claimSetup, type Me } from "./api";

// First-time setup: only while the platform has no account. The code is in
// the orchestrator's log, so only someone who runs the platform can become its first admin.

export function FirstSetup({ onDone }: { onDone: (me: Me) => void }) {
  const [code, setCode] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const problem = !code.trim() ? "the setup code" : !username.trim() ? "a username" : password.length < 8
    ? "a password of at least 8 characters" : password !== again ? "the same password twice" : null;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onDone(await claimSetup(code, username.trim(), password));
      window.location.hash = "#/welcome";
    } catch (err) {
      setError((err instanceof Error ? err.message : String(err)).replace(/^\d+: /, ""));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login setup">
      <form onSubmit={submit}>
        <h1>Welcome</h1>
        <p className="muted">This platform has no account yet. Create its first admin.</p>
        <label>
          Setup code
          <input className="mono" autoFocus value={code} onChange={(e) => setCode(e.target.value)} placeholder="XXXX-XXXX" autoComplete="off" />
          <small className="muted">
            In the orchestrator's log: <code>sudo docker compose logs orchestrator | grep SETUP</code>
          </small>
        </label>
        <label>Your username<input autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} /></label>
        <label>Password<input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        <label>Password again<input type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} /></label>
        {error && <p className="error">{error}</p>}
        <button type="submit" className="primary" disabled={busy || problem !== null}>
          {busy ? "Creating…" : problem ? `Give ${problem}` : "Create the first admin"}
        </button>
      </form>
    </div>
  );
}

/** After setup: what to do next, in order. */
export function Welcome() {
  return (
    <section className="welcome">
      <div className="toolbar"><h1>Welcome: three steps to a working platform</h1></div>
      <ol className="welcome-steps">
        <li className="done" data-tour="welcome-account">
          <span className="n">✓</span>
          <div><b>Your admin account</b><p className="muted">Create accounts for your team in Admin › Accounts.</p></div>
          <a className="button" href="#/admin/users">Accounts</a>
        </li>
        <li data-tour="welcome-credentials">
          <span className="n">2</span>
          <div><b>Switch credentials</b><p className="muted">The login the platform uses on the switches, the read-only login of the collector, the SNMP community and the M-LAG key. You can do it later.</p></div>
          <a className="button" href="#/admin/settings">Settings</a>
        </li>
        <li data-tour="welcome-fabric">
          <span className="n">3</span>
          <div><b>Your first fabric</b><p className="muted">Design a new one and place its switches on a map, or bring a running fabric under the platform.</p></div>
          <span className="inline">
            <a className="button primary" href="#/fabric/new">New fabric</a>
            <a className="button" href="#/fabric/import">Import a fabric</a>
          </span>
        </li>
      </ol>
      <p className="muted"><a href="#/overview">Skip for now</a>: everything here is also in the menu at the top.</p>
    </section>
  );
}
