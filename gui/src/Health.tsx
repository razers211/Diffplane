import { useEffect } from "react";
import { fetchHealth, type FabricHealth, type HealthLevel, type Scored } from "./api";
import { href } from "./nav";
import { useFetch } from "./useFetch";

// Health scores: 100 less a fixed amount per problem (40, 20 or 10), never
// below 0; 90+ good, 60-89 warning, under 60 critical. The fabric's score is its worst switch's or link's.

const REFRESH_MS = 30_000;
export const LEVEL_PILL: Record<HealthLevel, string> = { good: "pill good", warning: "pill warnpill", critical: "pill bad" };

/** A score on a half ring, coloured by level. */
export function ScoreGauge({ score, level, size = 120 }: { score: number; level: HealthLevel; size?: number }) {
  const r = 42, length = Math.PI * r;
  return (
    <svg className={`gauge ${level}`} viewBox="0 0 100 58" width={size} height={size * 0.58} role="img" aria-label={`score ${score} of 100, ${level}`}>
      <path className="track" d="M8 50a42 42 0 0 1 84 0" />
      <path className="value" d="M8 50a42 42 0 0 1 84 0" strokeDasharray={`${(score / 100) * length} ${length}`} />
      <text x="50" y="48" textAnchor="middle" className="num">{score}</text>
    </svg>
  );
}

function Reasons({ h }: { h: { reasons: { points: number; text: string }[] } }) {
  if (h.reasons.length === 0) return <span className="faint">nothing wrong</span>;
  return (
    <ul className="reasons">
      {h.reasons.map((r, i) => <li key={i}><span className="num points">{r.points}</span> {r.text}</li>)}
    </ul>
  );
}

function ScoreTable({ rows, name, linkTo }: { rows: Scored[]; name: string; linkTo?: (row: Scored) => string }) {
  return (
    <div className="tablewrap">
      <table className="devices health">
        <thead><tr><th>{name}</th><th>Score</th><th>Why</th></tr></thead>
        <tbody>
          {[...rows].sort((a, b) => a.score - b.score || a.name.localeCompare(b.name)).map((h) => (
            <tr key={h.name}>
              <td className="mono">{linkTo ? <a href={linkTo(h)}>{h.name}</a> : h.name}</td>
              <td>
                <div className="load">
                  <span className={`track score ${h.level}`}><i style={{ width: `${h.score}%` }} /></span>
                  <span className={LEVEL_PILL[h.level]}>{h.score}</span>
                </div>
              </td>
              <td><Reasons h={h} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function HealthPage({ site }: { site: string }) {
  const data = useFetch(() => (site ? fetchHealth(site) : Promise.resolve(null)), [site]);
  useEffect(() => {
    const id = window.setInterval(data.reload, REFRESH_MS);
    return () => window.clearInterval(id);
  }, [data.reload]);
  if (!site) return <p className="empty muted">Pick a fabric (top right): health is scored per fabric.</p>;
  if (data.error) return <p className="error">{data.error}</p>;
  if (!data.data) return <p className="muted">Loading…</p>;
  const h: FabricHealth = data.data;

  return (
    <div className="health-page">
      <section className="panel fabric-score" data-tour="fabric-score">
        <ScoreGauge score={h.score} level={h.level} size={150} />
        <div>
          <h2>{h.fabric}: <span className={LEVEL_PILL[h.level]}>{h.level}</span></h2>
          {h.worst ? (
            <p className="muted">
              Its worst {h.worst.kind} is <b className="mono">{h.worst.name}</b>
              {h.others_below_good > 0 && <>; {h.others_below_good} other{h.others_below_good > 1 ? "s" : ""} under 90</>}.
            </p>
          ) : <p className="muted">Every switch and link is fine.</p>}
          <Reasons h={h} />
          {h.planned.length > 0 && <p className="faint small">Not scored (planned): {h.planned.join(", ")}.</p>}
        </div>
      </section>
      <section className="panel" data-tour="health-switches">
        <header><h2>Switches</h2><span className="hint">active switches · refreshed every 30 s</span></header>
        {h.switches.length === 0 ? <p className="empty muted">No active switch.</p>
          : <ScoreTable rows={h.switches} name="Switch" linkTo={(row) => href.switchPage(row.name, "hardware")} />}
      </section>
      <div className="cols">
        <section className="panel" data-tour="health-links">
          <header><h2>Links</h2><span className="hint">between active switches</span></header>
          {h.links.length === 0 ? <p className="empty muted">No link between two active switches yet.</p> : <ScoreTable rows={h.links} name="Link" />}
        </section>
        <section className="panel" data-tour="health-segments">
          <header><h2>Segments</h2></header>
          {h.segments.length === 0 ? <p className="empty muted">No segment.</p> : <ScoreTable rows={h.segments} name="Segment" />}
        </section>
      </div>
    </div>
  );
}

/** A switch's score next to its name (switch page): fetched with its fabric's health. */
export function HealthBadge({ site, name }: { site: string; name: string }) {
  const data = useFetch(() => fetchHealth(site).catch(() => null), [site]);
  const h = data.data?.switches.find((s) => s.name === name);
  if (!h) return null;
  return (
    <a className={LEVEL_PILL[h.level]} href="#/overview/health"
       title={h.reasons.map((r) => `${r.points} ${r.text}`).join("\n") || "nothing wrong"}>
      health {h.score}
    </a>
  );
}
