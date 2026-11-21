import { useState } from "react";
import { fetchChecks, type DeployChecks, type FabricChecks } from "./api";

// Pre-deploy checks: problems in intent that a diff cannot show. Errors
// block approval until fixed; warnings are for the approver to weigh.

const clean = (e: unknown) => (e instanceof Error ? e.message : String(e)).replace(/^\d+: /, "");

export function Findings({ results }: { results: FabricChecks[] }) {
  const findings = results.flatMap((r) => r.findings.map((f) => ({ ...f, fabric: r.fabric })));
  const unknownLinks = results.some((r) => r.link_state === "unknown");
  if (findings.length === 0) {
    return <p className="empty good-text">No problem found{unknownLinks ? " (port state could not be read)" : ""}.</p>;
  }
  return (
    <ul className="findings">
      {findings.map((f, i) => (
        <li key={i} className={`finding ${f.severity}`}>
          <span className={f.severity === "error" ? "pill bad" : "pill warnpill"}>{f.severity === "error" ? "error" : "warning"}</span>
          <span>{f.text}{results.length > 1 && <span className="muted"> ({f.fabric})</span>}</span>
        </li>
      ))}
      {unknownLinks && <li className="muted small">Port state could not be read: services' ports were not checked.</li>}
    </ul>
  );
}

/** The checks of a fabric now, on request (before a deploy is prepared). */
export function FabricChecksPanel({ site }: { site: string }) {
  const [result, setResult] = useState<FabricChecks | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await fetchChecks(site));
    } catch (e) {
      setError(clean(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="panel" aria-label="Checks" data-tour="checks">
      <header>
        <h2>Checks</h2>
        <span className="hint">problems a diff cannot show; errors block approval</span>
        <button className="small" onClick={run} disabled={busy}>{busy ? "Checking…" : result ? "Run again" : "Run checks"}</button>
      </header>
      {error && <p className="error empty">{error}</p>}
      {result ? <Findings results={[result]} /> : <p className="empty muted">They also run when the deploy is prepared and when it is approved.</p>}
    </section>
  );
}

/** A deploy's checks, with "check again" (approval runs them again anyway). */
export function useJobChecks(checks: DeployChecks | null | undefined) {
  const [fresh, setFresh] = useState<FabricChecks[] | null>(null);
  const [busy, setBusy] = useState(false);
  const results = fresh ?? checks?.fabrics ?? [];
  const again = async () => {
    setBusy(true);
    try {
      setFresh(await Promise.all((checks?.fabrics ?? []).map((r) => fetchChecks(r.fabric))));
    } finally {
      setBusy(false);
    }
  };
  return { results, blocking: results.some((r) => r.blocking), again, busy, known: !!checks };
}
