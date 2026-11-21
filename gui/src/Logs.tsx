import { useState } from "react";
import { fetchDeviceLogs } from "./api";
import { useFetch } from "./useFetch";

// A switch's syslog messages: what it sent while the fabric's syslog is on,
// warnings and worse, newest first, kept 90 days. Read from ClickHouse when the tab asks.

const SEVERITIES: [number, string][] = [[7, "everything received"], [4, "warnings and worse"], [3, "errors and worse"], [2, "critical and worse"]];
const SPANS: [string, number][] = [["last hour", 1], ["last 24 hours", 24], ["last 7 days", 168], ["last 90 days", 2160]];

export const severityTone = (severity: number) => (severity <= 3 ? "pill bad" : severity === 4 ? "pill warnpill" : "pill");

export function Logs({ name }: { name: string }) {
  const [severity, setSeverity] = useState(7);
  const [hours, setHours] = useState(24);
  const [typed, setTyped] = useState("");
  const [text, setText] = useState("");
  const logs = useFetch(() => fetchDeviceLogs(name, { severity, text, hours }), [name, severity, text, hours]);

  return (
    <section className="panel" data-tour="logs">
      <header>
        <h2>Logs</h2>
        <span className="hint">syslog from {name}, newest first (times in UTC)</span>
      </header>
      <div className="pad">
        <form className="form-row" onSubmit={(e) => { e.preventDefault(); setText(typed.trim()); }}>
          <label>Severity
            <select value={severity} onChange={(e) => setSeverity(Number(e.target.value))}>
              {SEVERITIES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
            </select>
          </label>
          <label>Since
            <select value={hours} onChange={(e) => setHours(Number(e.target.value))}>
              {SPANS.map(([label, h]) => <option key={h} value={h}>{label}</option>)}
            </select>
          </label>
          <label>Text
            <input value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="e.g. 10GE1/0/10 or IFNET" spellCheck={false} />
          </label>
          <button type="submit">Search</button>
          <button type="button" onClick={logs.reload}>Refresh</button>
        </form>
        {logs.error && <p className="error">{logs.error}</p>}
        {logs.data && logs.data.length === 0 && (
          <p className="empty muted">No message. Turn syslog on for this fabric on the Fabric page, then deploy.</p>
        )}
        {logs.data && logs.data.length > 0 && (
          <div className="tablewrap">
            <table className="devices">
              <thead><tr><th>Time</th><th>Severity</th><th>Module</th><th>Message</th></tr></thead>
              <tbody>
                {logs.data.map((m, i) => (
                  <tr key={`${m.time}-${i}`}>
                    <td className="mono small">{m.time}</td>
                    <td><span className={severityTone(m.severity)}>{m.severity_name}</span></td>
                    <td className="mono small">{m.tag}</td>
                    <td className="mono small">{m.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {logs.data && logs.data.length >= 500 && <p className="faint small">The newest 500: narrow the search to see older ones.</p>}
      </div>
    </section>
  );
}
