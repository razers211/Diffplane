import { useState } from "react";
import { fetchSwitchMicroseg } from "./api";
import { share, type SwitchState } from "./microseg";

// Microsegmentation as one switch has it: read on demand over NETCONF, nothing stored.

export function useSwitchState() {
  const [state, setState] = useState<SwitchState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const read = async (device: string) => {
    setBusy(true);
    setError(null);
    try {
      setState(await fetchSwitchMicroseg(device));
    } catch (e) {
      setState(null);
      setError(e instanceof Error ? e.message.replace(/^\d+: /, "") : String(e));
    } finally {
      setBusy(false);
    }
  };
  return { state, busy, error, read };
}

/** What the switch could not install: rules and members not in state "success". */
export function Failures({ state }: { state: SwitchState }) {
  if (!state.failed_rules.length && !state.failed_members.length) return null;
  return (
    <div className="notice error-box">
      <b>{state.device} could not install:</b>
      <ul>
        {state.failed_rules.map((r) => <li key={`${r.classifier}-${r.rule}`} className="mono">{r.classifier} rule {r.rule}: {r.state}</li>)}
        {state.failed_members.map((m) => <li key={`${m.epg}-${m.member}-${m.vpn}`} className="mono">EPG {m.epg} {m.member} in {m.vpn}: {m.state}</li>)}
      </ul>
    </div>
  );
}

/** The switch tab Hardware's panel: microsegmentation's share of each chip's TCAM. */
export function MicrosegTcam({ name }: { name: string }) {
  const { state, busy, error, read } = useSwitchState();
  return (
    <section className="panel" data-tour="hardware-tcam">
      <header>
        <h2>Microsegmentation TCAM</h2>
        <span className="hint">per chip · read now from the switch</span>
        <button disabled={busy} onClick={() => read(name)}>{busy ? "Reading…" : state ? "Read again" : "Read"}</button>
      </header>
      {error && <p className="error">{error}</p>}
      {state && (
        <>
          <div className="tablewrap">
            <table className="devices">
              <thead><tr><th>Chip</th><th>TCAM blocks</th><th className="num">Groups</th><th className="num">Members</th><th className="num">Rules</th></tr></thead>
              <tbody>
                {state.chips.map((c) => (
                  <tr key={`${c.slot}-${c.chip}`}>
                    <td className="mono small">slot {c.slot}, chip {c.chip}</td>
                    <td>
                      <span className="load" title={`${c.tcam_block_used} of the ${c.tcam_block_limit} blocks microsegmentation may use (${c.tcam_block_total} on the chip)`}>
                        <span className="track"><i style={{ width: `${share(c.tcam_block_used, c.tcam_block_limit)}%` }} /></span>
                        <span className="mono small">{c.tcam_block_used} / {c.tcam_block_limit}</span>
                      </span>
                    </td>
                    <td className="num mono">{c.epg_used} / {c.epg_used + c.epg_free}</td>
                    <td className="num mono">{c.epg_member_used} / {c.epg_member_used + c.epg_member_free}</td>
                    <td className="num mono">{c.gbp_rule_used} / {c.gbp_rule_used + c.gbp_rule_free}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {state.chips.length === 0 && <p className="empty muted">Microsegmentation is not on this switch.</p>}
          <Failures state={state} />
        </>
      )}
    </section>
  );
}
