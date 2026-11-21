import { useState } from "react";
import { fetchOspfAuth, rollOspfKey, setOspfAuth } from "./api";
import { nextStep } from "./ospfAuth";
import { useCan } from "./role";
import { useFetch } from "./useFetch";

// Link authentication: generated OSPF keys on fabric links. Turned on or off
// only while no fabric link is up; a key change is three deploys. No key is ever shown.

const clean = (e: unknown) => (e instanceof Error ? e.message : String(e)).replace(/^\d+: /, "");

export function FabricOspfAuth({ site, onSaved }: { site: string; onSaved: (text: string) => void }) {
  const canOperate = useCan("operator");
  const auth = useFetch(() => fetchOspfAuth(site), [site]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const a = auth.data;
  if (auth.error) return <p className="error">{auth.error}</p>;
  if (!a) return <p className="muted">Loading…</p>;
  const step = nextStep(a);

  const act = async (call: () => Promise<unknown>, text: string) => {
    setError(null);
    setBusy(true);
    try {
      await call();
      setConfirming(false);
      auth.reload();
      onSaved(text);
    } catch (e) {
      setError(clean(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <p>{a.enabled ? "On: fabric links only talk to switches holding this fabric's secret key." : "Off: any switch cabled to a fabric link is trusted."}</p>
      <p className="muted">
        The platform makes the keys and never shows them. Turning this on or off changes every link at once, so it is only allowed while no fabric link is up.
      </p>
      {canOperate && !confirming && (
        <button type="button" disabled={busy} onClick={() => setConfirming(true)}>{a.enabled ? "Turn off" : "Turn on"}</button>
      )}
      {canOperate && confirming && (
        <p role="alert">
          {a.enabled ? "Turn off" : "Turn on"} for every switch at the next deploy? Links between switches that differ would stay down.{" "}
          <button type="button" disabled={busy} onClick={() => act(() => setOspfAuth(site, !a.enabled), `Link authentication ${a.enabled ? "off" : "on"}: deploy it.`)}>Yes</button>{" "}
          <button type="button" disabled={busy} onClick={() => setConfirming(false)}>Cancel</button>
        </p>
      )}
      {step && canOperate && (
        <p>
          <button type="button" disabled={busy} onClick={() => act(() => rollOspfKey(site, step.action), `${step.label}: deploy it.`)}>{step.label}</button>{" "}
          <span className="muted">{step.text}</span>
        </p>
      )}
      {error && <p className="error">{error}</p>}
    </>
  );
}
