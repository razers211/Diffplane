import { useState } from "react";
import { approveZtp, fetchZtp, fetchZtpFiles, registerZtp, unregisterZtp } from "./api";
import { KeyRows } from "./HostKeys";
import { useCan, useMayApprove } from "./role";
import { useFetch } from "./useFetch";

// Zero-touch provisioning of a planned switch: register its MEth MAC,
// management address and gateway; the switch boots blank, gets its address and first-boot config
// from the platform's DHCP/TFTP; the orchestrator fetches its host keys once it answers SSH; an
// approver compares them with the console and approves, which pins them and activates the switch.

const clean = (e: unknown) => (e instanceof Error ? e.message : String(e)).replace(/^\d+: /, "");

export function ZtpPanel({ name, site, mac, onActivated }: { name: string; site?: string | null; mac?: string | null; onActivated: () => void }) {
  const ztp = useFetch(fetchZtp, []);
  const canOperate = useCan("operator");
  const canApprove = useMayApprove(site);   // approvers, or an operator allowed on this fabric
  const [form, setForm] = useState({ mac: mac ?? "", address: "", gateway: "" });   // MAC from the inventory
  const [files, setFiles] = useState<{ intermediate: string; config: string } | null>(null);
  const [compared, setCompared] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reg = ztp.data?.registrations[name];

  const act = async (call: () => Promise<unknown>, after?: () => void) => {
    setError(null);
    setBusy(true);
    try {
      await call();
      after?.();
      ztp.reload();
    } catch (e) {
      setError(clean(e));
    } finally {
      setBusy(false);
    }
  };
  const reported = reg?.offered_keys;
  const keys = reg?.offered ? [...new Set([...reg.offered["22"], ...reg.offered["830"]])] : [];

  return (
    <div className="ztp">
      <div className="inline">
        <h3>Zero-touch provisioning</h3>
        {ztp.data && !ztp.data.configured && <span className="pill warnpill">DHCP not configured</span>}
      </div>
      {ztp.data && !ztp.data.configured && (
        <p className="muted small">
          Set ZTP_DHCP_INTERFACE, ZTP_DHCP_NETWORK and ZTP_SERVER_ADDRESS in deploy/.env and start the ztp profile
          (operations guide, "Zero-touch provisioning"). Registrations are kept meanwhile.
        </p>
      )}
      {ztp.error && <p className="error small">{ztp.error}</p>}
      {!reg && ztp.data && canOperate && (
        <div className="form-row">
          <label>MEth MAC<input className="mono" value={form.mac} onChange={(e) => setForm({ ...form, mac: e.target.value })} placeholder="0200-fc12-3456" /></label>
          <label>Management address<input className="mono" value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} placeholder="172.24.0.110/24" /></label>
          <label>Gateway<input className="mono" value={form.gateway} onChange={(e) => setForm({ ...form, gateway: e.target.value })} placeholder="172.24.0.1" /></label>
          <button className="primary" disabled={busy || !form.mac || !form.address || !form.gateway}
            onClick={() => act(() => registerZtp(name, form))}>Register</button>
        </div>
      )}
      {!reg && ztp.data && !canOperate && <p className="muted small">Not registered.</p>}
      {reg && (
        <>
          <p className="small">
            Registered: MAC <span className="mono">{reg.mac}</span>, <span className="mono">{reg.address}</span> via{" "}
            <span className="mono">{reg.gateway}</span> on {reg.interface}.{" "}
            {reg.offered
              ? <b>The switch is up and reported its host keys{reg.reported_at ? ` (${new Date(reg.reported_at * 1000).toLocaleString()})` : ""}.</b>
              : reg.expired
                ? <b>Its provisioning window has ended: the platform no longer answers its DHCP request or serves its files. Register it again to retry.</b>
                : `Waiting for the switch to boot and answer SSH at that address${reg.expires_at ? ` (served until ${new Date(reg.expires_at * 1000).toLocaleString()})` : ""}.`}
          </p>
          <div className="inline">
            {canOperate && (
              <button className="small" onClick={() => act(async () => setFiles(files ? null : await fetchZtpFiles(name)))}>
                {files ? "Hide files" : "Show its files"}
              </button>
            )}
            {canOperate && reg.expired && (
              <button className="small" disabled={busy}
                onClick={() => act(() => registerZtp(name, { mac: reg.mac, address: reg.address, gateway: reg.gateway, interface: reg.interface }))}>
                Register again
              </button>
            )}
            {canOperate && <button className="small" disabled={busy} onClick={() => act(() => unregisterZtp(name), () => setFiles(null))}>Unregister</button>}
          </div>
          {files && (
            <div className="ztp-files">
              <b className="small">ztp.ini (intermediate file)</b>
              <pre className="config">{files.intermediate}</pre>
              <b className="small">{name}.cfg (first-boot config, passwords masked)</b>
              <pre className="config">{files.config}</pre>
            </div>
          )}
          {reported && (
            <div className="offer">
              <p className="muted small">
                Compare these fingerprints with the switch's own display of its host key on its console
                (<code>display rsa local-key-pair public</code>) before approving. Approving pins them and activates {name} at{" "}
                {reg.address}; the keys are fetched again first and must be exactly these.
              </p>
              <div className="key-ports">
                <div><b className="small">Port 22 · SSH</b><KeyRows keys={reported["22"]} /></div>
                <div><b className="small">Port 830 · NETCONF</b><KeyRows keys={reported["830"]} /></div>
              </div>
              {canApprove ? (
                <div className="inline">
                  <label className="check small">
                    <input type="checkbox" checked={compared} onChange={(e) => setCompared(e.target.checked)} />
                    I compared these fingerprints with the switch's console
                  </label>
                  <button className="primary small" disabled={!compared || busy}
                    onClick={() => act(() => approveZtp(name, keys), onActivated)}>Approve and activate</button>
                </div>
              ) : (
                <p className="muted small">Approving needs the approver role.</p>
              )}
            </div>
          )}
        </>
      )}
      {error && <p className="error small">{error}</p>}
    </div>
  );
}
