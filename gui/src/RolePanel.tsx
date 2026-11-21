import { useState } from "react";
import { setDeviceRole, type Device } from "./api";
import { useCan } from "./role";
import { Term } from "./Term";

// A leaf's role: leaf <-> border-leaf, both switches of a pair together.
// The orchestrator refuses it while a port carries segments (a border leaf only routes).

const clean = (e: unknown) => (e instanceof Error ? e.message : String(e)).replace(/^\d+: /, "");

export function RolePanel({ device, onChanged }: { device: Device; onChanged: () => void }) {
  const canOperate = useCan("operator");
  const [confirm, setConfirm] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (device.role !== "leaf" && device.role !== "border-leaf") return null;
  const next = device.role === "leaf" ? "border-leaf" : "leaf";

  const change = async () => {
    setError(null);
    try {
      const r = await setDeviceRole(device.name, next);
      setResult(`${r.devices.join(" and ")} ${r.devices.length > 1 ? "are" : "is"} now ${r.role}. Review the change before deploying.`);
      setConfirm(false);
      onChanged();
    } catch (e) {
      setError(clean(e));
    }
  };

  return (
    <div className="panel" data-tour="role">
      <header>
        <h2>Role</h2>
        <span className="hint">its M-LAG peer changes with it</span>
        {canOperate && !confirm && <button onClick={() => { setConfirm(true); setResult(null); }}>Make it a {next}</button>}
      </header>
      <p className="empty">
        <span className="pill"><Term k={device.role}>{device.role}</Term></span>{" "}
        {next === "border-leaf"
          ? "A border leaf only routes: it gets every routed segment's VPN instance, and no segment may be on its ports."
          : "As a plain leaf it keeps only the VPN instances of segments attached or firewalled there."}
      </p>
      {confirm && (
        <p className="empty">
          This changes the generated config of the pair (shown as pending changes, deployed as usual).{" "}
          <button className="small primary" onClick={change}>Change to {next}</button>{" "}
          <button className="small" onClick={() => setConfirm(false)}>Cancel</button>
        </p>
      )}
      {result && <p className="notice empty">{result}</p>}
      {error && <p className="error empty">{error}</p>}
    </div>
  );
}
