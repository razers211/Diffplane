import { useState, type FormEvent } from "react";
import { fetchKnownHosts, fetchReach, type KnownHost, type Reach, type TraceResult } from "./api";
import { CHIPS, checkResult, drawnPath, fixLabel, traffic, type Chip } from "./reach";
import { TopologyView } from "./TopologyView";

// Reachability (built on the path trace): two hosts and what traffic; the first place it stops
// and why, or the path both ways. The switches are read when asked, nothing is stored or changed.

export const RESULT: Record<TraceResult, { text: string; pill: string }> = {
  delivered: { text: "reaches the destination", pill: "pill good" },
  gateway: { text: "ends at the segment's gateway", pill: "pill good" },
  firewall: { text: "ends at a firewall", pill: "pill warnpill" },
  isp: { text: "leaves the fabric to an ISP", pill: "pill warnpill" },
  drop: { text: "is dropped", pill: "pill bad" },
  unknown: { text: "cannot be followed to the end", pill: "pill" },
};

export function PathTrace({ site }: { site: string }) {
  const [source, setSource] = useState("");
  const [destination, setDestination] = useState("");
  const [chip, setChip] = useState<Chip>("Anything");
  const [proto, setProto] = useState("tcp");
  const [port, setPort] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Reach | null>(null);
  const [expert, setExpert] = useState(false);
  const [hosts, setHosts] = useState<KnownHost[] | null>(null);

  if (!site) return <p className="empty muted">Pick a fabric (top right): a check stays within one fabric.</p>;

  const suggest = () => {
    if (hosts !== null) return;
    setHosts([]);   // asked once: reading every switch takes seconds
    fetchKnownHosts(site).then((r) => setHosts(r.hosts)).catch(() => undefined);   // typing still works
  };

  const run = async (e: FormEvent) => {
    e.preventDefault();
    const wanted = traffic(chip, proto, port);
    if (typeof wanted === "string") {
      setError(wanted);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setResult(await fetchReach(site, source.trim(), destination.trim(), wanted.proto, wanted.port));
    } catch (err) {
      setResult(null);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const drawn = result ? drawnPath(result) : null;
  return (
    <div className="trace-page">
      <section className="panel">
        <form onSubmit={run} data-tour="trace-form">
          <div className="trace-form">
            <label>From
              <input list="reach-hosts" value={source} onChange={(e) => setSource(e.target.value)} onFocus={suggest}
                     placeholder="IP, MAC or switch" required spellCheck={false} />
            </label>
            <label>To
              <input list="reach-hosts" value={destination} onChange={(e) => setDestination(e.target.value)} onFocus={suggest}
                     placeholder="IP, MAC or switch" required spellCheck={false} />
            </label>
            <datalist id="reach-hosts">
              {(hosts ?? []).map((h) => <option key={h.value} value={h.value}>{h.label}</option>)}
            </datalist>
            <button className="primary" disabled={busy}>{busy ? "Reading the switches…" : "Check"}</button>
          </div>
          <div className="reach-traffic">
            <span className="toggle" role="group" aria-label="What traffic?">
              {CHIPS.map(({ chip: c, hint }) => (
                <button type="button" key={c} title={hint} aria-pressed={chip === c} onClick={() => setChip(c)}>{c}</button>
              ))}
            </span>
            {chip === "Expert" && <>
              <label>Protocol
                <select value={proto} onChange={(e) => setProto(e.target.value)}>
                  {["tcp", "udp", "icmp", "any"].map((p) => <option key={p}>{p}</option>)}
                </select>
              </label>
              {(proto === "tcp" || proto === "udp") && (
                <label>Port <input value={port} onChange={(e) => setPort(e.target.value)} inputMode="numeric" placeholder="any" /></label>
              )}
            </>}
          </div>
        </form>
        {error && <p className="error">{error}</p>}
      </section>
      {result && (
        <section className="panel">
          <div className={`reach-answer ${result.verdict}`} data-tour="reach-answer" role="status">
            <strong>{result.sentence}</strong>
            {result.stop?.page && <a href={result.stop.page}>{fixLabel(result.stop.page)} →</a>}
          </div>
          {result.unread.length > 0 && (
            <p className="warning">Could not read: {result.unread.join("; ")}. The answer went on without them.</p>
          )}
          <header>
            <button className="linklike small" aria-expanded={expert} onClick={() => setExpert(!expert)}>
              {expert ? "Hide Expert view" : "Expert view"}
            </button>
            <span className="hint">{result.architecture} fabric · read from {result.read.length ? result.read.join(", ") : "no switch"}</span>
          </header>
          {expert && <>
            <div className="tablewrap">
              <table className="devices">
                <thead><tr><th>Way</th><th>Check</th><th>Result</th><th>What it found</th><th>Source</th></tr></thead>
                <tbody>
                  {result.checks.map((c, i) => (
                    <tr key={i}><td>{c.direction}</td><td>{c.check}</td><td>{checkResult(c)}</td><td>{c.text}</td><td>{c.from}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
            {(["there", "back"] as const).map((way) => (
              <div key={way}>
                <p className="reach-way"><strong>{way === "there" ? "There" : "Back"}</strong>{" "}
                  <span className={RESULT[result[way].result].pill}>{RESULT[result[way].result].text}</span></p>
                <ol className="trace-hops">
                  {result[way].hops.map((h, i) => (
                    <li key={i} className={`k-${h.kind}`}><span>{h.text}</span><span className="from">{h.from}</span></li>
                  ))}
                </ol>
              </div>
            ))}
          </>}
          <p className="faint small">From the fabric's intent and what the switches show now; real forwarding is not tested.</p>
        </section>
      )}
      <TopologyView site={site} title="On the map" path={drawn?.path} stop={drawn?.stop} />
    </div>
  );
}
