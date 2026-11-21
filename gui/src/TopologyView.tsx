import { useEffect, useState } from "react";
import { fetchArchitecture, fetchHealth, fetchTopology, fetchTraffic, type DeployStep, type FabricTraffic, type LinkEnd, type Scored, type TopologyLink } from "./api";
import { LEVEL_PILL } from "./Health";
import { bundleLoad, flowSeconds, layout, mapLabel, problemCount, type Bundle, type PlacedNode } from "./topology";
import { formatBps } from "./units";
import { useFetch } from "./useFetch";

// The fabric map: switch boxes with a status stripe on pair floors. Plain SVG. It is a panel of the
// overview (layout A): details open beside the map when something is clicked.

const REFRESH_MS = 30_000;
const REDUCED_MOTION = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
const TRAFFIC_MS = 10_000; // counters are polled every 5 s; a calm refresh for the map
const ZOOMS = [1, 1.5, 2, 3];

type Selection = { kind: "node"; name: string } | { kind: "bundle"; id: string } | null;

/** A deploy's step per switch: the map shows each switch's progress while it rolls out. */
export type DeployMarks = Record<string, DeployStep["status"]>;

export function TopologyView({ site, deploy, title = "Fabric", path, stop }:
  { site: string; deploy?: DeployMarks; title?: string; path?: string[]; stop?: string }) {
  const { data, error, reload } = useFetch(() => fetchTopology(site || undefined), [site]);
  const arch = useFetch(() => (site ? fetchArchitecture(site) : Promise.resolve(null)), [site]);
  const [selected, setSelected] = useState<Selection>(null);
  const [zoom, setZoom] = useState(0);
  const [view, setView] = useState<"state" | "traffic">("traffic");
  const traffic = useFetch(() => (view === "traffic" ? fetchTraffic(site || undefined) : Promise.resolve(null)), [site, view]);
  // Each switch's health score as a badge, when one fabric is shown.
  const health = useFetch(() => (site ? fetchHealth(site).catch(() => null) : Promise.resolve(null)), [site]);
  const scores = Object.fromEntries((health.data?.switches ?? []).map((h) => [h.name, h]));

  useEffect(() => {
    const id = window.setInterval(() => { reload(); health.reload(); }, REFRESH_MS);
    return () => window.clearInterval(id);
  }, [reload, health.reload]);
  useEffect(() => {
    if (view !== "traffic") return;
    const id = window.setInterval(traffic.reload, TRAFFIC_MS);
    return () => window.clearInterval(id);
  }, [view, traffic.reload]);
  const rates: FabricTraffic | null = view === "traffic" ? traffic.data?.interfaces ?? {} : null;

  const map = data ? layout(data, arch.data?.gateway_pair ?? null) : null;
  const problems = data ? problemCount(data) : 0;
  const node = selected?.kind === "node" ? map?.nodes.find((n) => n.name === selected.name) : undefined;
  const bundle = selected?.kind === "bundle" ? map?.bundles.find((b) => b.id === selected.id) : undefined;

  return (
    <section className="panel topology" aria-label="Fabric map">
      <header>
        <h2>{title}</h2>
        {data && (
          <span className={problems ? "pill bad" : "pill good"}>
            {problems ? `${problems} cabling problem${problems > 1 ? "s" : ""}` : "Cabling matches LLDP"}
          </span>
        )}
        <span className="toggle" role="group" aria-label="Colour lines by">
          <button aria-pressed={view === "state"} onClick={() => setView("state")}>State</button>
          <button aria-pressed={view === "traffic"} onClick={() => setView("traffic")}>Traffic</button>
        </span>
        <span className="zoom" role="group" aria-label="Zoom">
          <button onClick={() => setZoom((z) => Math.max(0, z - 1))} disabled={zoom === 0} aria-label="Zoom out">−</button>
          <span className="num">{Math.round(ZOOMS[zoom] * 100)} %</span>
          <button onClick={() => setZoom((z) => Math.min(ZOOMS.length - 1, z + 1))} disabled={zoom === ZOOMS.length - 1} aria-label="Zoom in">+</button>
        </span>
      </header>
      {error && <p className="error">Could not load topology: {error}</p>}
      {data && !data.telemetry.ok && (
        <p className="warning">No live data ({data.telemetry.error}). Links are shown from NetBox only.</p>
      )}
      {view === "traffic" && traffic.data && !traffic.data.telemetry.ok && (
        <p className="warning">No traffic data ({traffic.data.telemetry.error}).</p>
      )}
      <div className={node || bundle ? "split" : "whole"}>
        <div className="map" onClick={() => setSelected(null)}>
          {map && map.nodes.length === 0 && <p className="muted">No devices in this fabric yet.</p>}
          {map && map.nodes.length > 0 && (
            <svg
              viewBox={`0 0 ${map.width} ${map.height}`}
              // Fills the panel, but never more than 1.6 times its natural size: a small fabric
              // is not blown up (1.6 on the overview).
              style={{ width: `${ZOOMS[zoom] * 100}%`, maxWidth: `${map.width * 1.6 * ZOOMS[zoom]}px` }}
              role="img"
              aria-label={`Topology${site ? ` of ${site}` : ""}`}
            >
              {map.bands.length > 1 &&
                map.bands.map((band) => (
                  <text key={band.site ?? "none"} className="band-label" x={12} y={band.y + 22}>
                    {band.site ?? "no fabric"}
                  </text>
                ))}
              {map.floors.map((f, i) => (
                <g key={i}>
                  <path
                    className={`floor${f.gateway ? " gw" : ""}`}
                    d={`M${f.cx - f.w / 2 + 10} ${f.cy - f.h / 2}H${f.cx + f.w / 2 - 10}Q${f.cx + f.w / 2} ${f.cy - f.h / 2} ${f.cx + f.w / 2} ${f.cy - f.h / 2 + 10}V${f.cy + f.h / 2 - 10}Q${f.cx + f.w / 2} ${f.cy + f.h / 2} ${f.cx + f.w / 2 - 10} ${f.cy + f.h / 2}H${f.cx - f.w / 2 + 10}Q${f.cx - f.w / 2} ${f.cy + f.h / 2} ${f.cx - f.w / 2} ${f.cy + f.h / 2 - 10}V${f.cy - f.h / 2 + 10}Q${f.cx - f.w / 2} ${f.cy - f.h / 2} ${f.cx - f.w / 2 + 10} ${f.cy - f.h / 2}Z`}
                  />
                  <text className={f.gateway || f.label.includes("·") ? "floor-label" : "floor-label dim"} x={f.cx} y={f.cy + f.h / 2 + 16} textAnchor="middle">
                    {f.label}
                  </text>
                </g>
              ))}
              {map.bundles.map((b) => (
                <BundleLine
                  key={b.id}
                  bundle={b}
                  rates={rates}
                  nodes={map.nodes}
                  selected={selected?.kind === "bundle" && selected.id === b.id}
                  onSelect={() => setSelected({ kind: "bundle", id: b.id })}
                />
              ))}
              {path && path.length > 0 && <TracePath path={path} stop={stop} nodes={map.nodes} />}
              {map.nodes.map((n) => (
                <SwitchBox
                  key={n.name}
                  placed={n}
                  mark={deploy?.[n.node.name]}
                  health={scores[n.node.name]}
                  selected={selected?.kind === "node" && selected.name === n.name}
                  onSelect={() => setSelected({ kind: "node", name: n.name })}
                />
              ))}
            </svg>
          )}
        </div>
        {(node || bundle) && (
          <aside className="details">
            <button className="small close" onClick={() => setSelected(null)} aria-label="Close details">×</button>
            {node ? <NodeDetails placed={node} health={scores[node.node.name]} /> : bundle && <BundleDetails bundle={bundle} rates={rates} />}
          </aside>
        )}
      </div>
      <details className="map-legend">
        <summary>Legend</summary>
        {view === "traffic" ? <TrafficLegend /> : <Legend />}
      </details>
    </section>
  );
}

const anchor = (n: PlacedNode, other: PlacedNode, peer: boolean): [number, number] => {
  if (peer) return [n.x + (other.x > n.x ? 1 : -1) * (n.kind === "spine" ? 30 : 22), n.y];
  if (n.kind === "spine") return [n.x, n.y + 12];
  return [n.x, other.y < n.y ? n.y - 11 : n.y + 18];
};

function BundleLine({ bundle, rates, nodes, selected, onSelect }: { bundle: Bundle; rates: FabricTraffic | null; nodes: PlacedNode[]; selected: boolean; onSelect: () => void }) {
  const a = nodes.find((n) => n.name === bundle.a);
  const b = nodes.find((n) => n.name === bundle.b);
  if (!a || !b) return null;
  const [x1, y1] = anchor(a, b, bundle.peer);
  const [x2, y2] = anchor(b, a, bundle.peer);
  const load = rates ? bundleLoad(bundle, rates) : null;
  const usage = load === null ? "" : load.load === null ? ", no traffic data" : `, ${Math.round(load.load * 100)} % (${formatBps(load.bps)})`;
  const label = `${bundle.a} – ${bundle.b}: ${bundle.links.length} link${bundle.links.length > 1 ? "s" : ""}, ${bundle.status}${usage}`;
  // Traffic view: one hue, width and depth by load (sequential); no data is a faint dotted line.
  const style = load && load.load !== null ? { strokeWidth: 1.6 + 6 * load.load, opacity: 0.35 + 0.65 * load.load } : undefined;
  const cls = load ? `bundle traffic${load.load === null ? " nodata" : ""}` : `bundle link-${bundle.status}${bundle.peer ? " peer" : ""}`;
  return (
    <g
      className={`${cls}${selected ? " selected" : ""}`}
      tabIndex={0}
      role="button"
      aria-label={label}
      onClick={(e) => {
        e.stopPropagation();
        onSelect();
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onSelect();
      }}
    >
      <title>{label}</title>
      <line className="hit" x1={x1} y1={y1} x2={x2} y2={y2} />
      <line className="wire" x1={x1} y1={y1} x2={x2} y2={y2} style={style} />
      {/* Traffic running along the link, both ways, faster when busier. */}
      {load && load.bps && !REDUCED_MOTION && [`M${x1},${y1} L${x2},${y2}`, `M${x2},${y2} L${x1},${y1}`].map((path, i) => (
        <circle key={i} className="flow" r={1.9}>
          <animateMotion dur={`${flowSeconds(load.load ?? 0.05)}s`} begin={`${i * 0.7}s`} repeatCount="indefinite" path={path} />
        </circle>
      ))}
    </g>
  );
}

function SwitchBox({ placed, selected, onSelect, mark, health }: { placed: PlacedNode; selected: boolean; onSelect: () => void; mark?: DeployStep["status"]; health?: Scored }) {
  const { x, y, kind, node } = placed;
  const spine = kind === "spine";
  const w = spine ? 30 : 22, depth = spine ? 13 : 11;
  const ports = spine ? 5 : 4;
  const state = !node.in_netbox ? "unknown" : node.status !== "active" ? "planned" : node.collected ? "polled" : "silent";
  const label = mapLabel(node);
  return (
    <g
      className={`dev ${state}${selected ? " selected" : ""}${mark ? ` dep dep-${mark}` : ""}`}
      tabIndex={0}
      role="button"
      aria-label={`${node.name}, ${node.role ?? "unknown role"}, ${state}`}
      onClick={(e) => {
        e.stopPropagation();
        onSelect();
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onSelect();
      }}
    >
      <title>{`${node.name} (${node.role ?? "not in NetBox"}, ${state})`}</title>
      <rect className="box" x={x - w} y={y - depth} width={w * 2} height={depth * 2} rx={4} />
      <rect className={`box-stripe ${state}`} x={x - w} y={y - depth} width={3.5} height={depth * 2} rx={1.5} />
      {Array.from({ length: ports }, (_, k) => (
        <rect key={k} className="box-port" x={x - w + 9 + k * ((w * 2 - 14) / ports)} y={y + depth - 6} width={3.2} height={2.8} rx={0.5} />
      ))}
      {state !== "planned" && !mark && <circle className={`badge ${state}`} cx={x + w - 4} cy={y - depth + 4} r={4.2} />}
      {mark && <DeployMark x={x} y={y} w={w} depth={depth} mark={mark} />}
      {health && !mark && (
        <g className={`score-chip ${health.level}`}>
          <rect x={x - w - 6} y={y - depth - 2} width={18} height={11} rx={5.5} />
          <text x={x - w + 3} y={y - depth + 6} textAnchor="middle">{health.score}</text>
        </g>
      )}
      <text className="dev-label" x={x} y={spine ? y - depth - 8 : y + depth + 13} textAnchor="middle">
        {label}
      </text>
    </g>
  );
}

/** A traced path: a line through its switches in order, each numbered; red where traffic stops. */
function TracePath({ path, stop, nodes }: { path: string[]; stop?: string; nodes: PlacedNode[] }) {
  const stops = path.map((name) => nodes.find((n) => n.node.name === name)).filter((n): n is PlacedNode => !!n);
  const d = stops.map((n, i) => `${i ? "L" : "M"}${n.x} ${n.y}`).join("");
  return (
    <g className="trace-path" aria-hidden="true">
      {stops.length > 1 && <path d={d} />}
      {stops.map((n, i) => (
        <g key={n.name} className={n.node.name === stop ? "trace-stop stopped" : "trace-stop"}>
          <circle cx={n.x + 26} cy={n.y - 18} r={8} />
          <text x={n.x + 26} y={n.y - 14.5} textAnchor="middle">{i + 1}</text>
        </g>
      ))}
    </g>
  );
}

/** A switch's place in a rollout: a patch dropping onto it, a ring while it changes, a check when verified. */
function DeployMark({ x, y, w, depth, mark }: { x: number; y: number; w: number; depth: number; mark: DeployStep["status"] }) {
  const busy = mark === "deploying" || mark === "verifying";
  return (
    <g className="dep-mark" aria-hidden="true">
      {busy && <ellipse className="dep-ring" cx={x} cy={y} rx={w + 8} ry={depth + 6} />}
      {mark === "deploying" && (
        <circle key="packet" className="dep-packet" r={4} cx={x}>
          <animate attributeName="cy" from={y - 90} to={y} dur="0.7s" fill="freeze" />
          <animate attributeName="opacity" values="1;1;0" dur="0.9s" fill="freeze" />
        </circle>
      )}
      {mark === "verified" && (
        <g className="dep-check">
          <circle cx={x} cy={y - depth - 4} r={7} />
          <path d={`M${x - 3.5} ${y - depth - 4}l2.5 2.5 4.5-5`} />
        </g>
      )}
      {mark === "failed" && (
        <g className="dep-failed">
          <circle cx={x} cy={y - depth - 4} r={7} />
          <path d={`M${x - 3} ${y - depth - 7}l6 6M${x + 3} ${y - depth - 7}l-6 6`} />
        </g>
      )}
    </g>
  );
}

function NodeDetails({ placed, health }: { placed: PlacedNode; health?: Scored }) {
  const { node } = placed;
  const state = !node.in_netbox ? "seen by LLDP only, not in NetBox" : node.status !== "active" ? "planned: modelled only, not polled" : node.collected ? "polled recently" : "active but not polled recently";
  return (
    <div>
      <h2 className="mono">{node.name}</h2>
      <dl>
        <dt>Role</dt>
        <dd>{node.role ?? "—"}</dd>
        <dt>Fabric</dt>
        <dd>{node.site ?? "—"}</dd>
        <dt>Status</dt>
        <dd>{node.status ?? "—"}</dd>
        <dt>Collector</dt>
        <dd>{state}</dd>
        {health && <><dt>Health</dt><dd><span className={LEVEL_PILL[health.level]}>{health.score}</span>{" "}
          <span className="small muted">{health.reasons.slice(0, 3).map((r) => r.text).join("; ")}{health.reasons.length > 3 ? "; …" : ""}</span></dd></>}
      </dl>
      {node.in_netbox && (
        <p>
          <a href={`#/switches/${encodeURIComponent(node.name)}`}>Open device: config and traffic →</a>
        </p>
      )}
    </div>
  );
}

function BundleDetails({ bundle, rates }: { bundle: Bundle; rates: FabricTraffic | null }) {
  return (
    <div>
      <h2>
        {bundle.a} – {bundle.b}
      </h2>
      <p className="muted">
        {bundle.links.length} link{bundle.links.length > 1 ? "s" : ""}
        {bundle.peer ? " between the two devices of a pair" : ""}
      </p>
      {bundle.links.map((link) => (
        <LinkDetails key={`${link.source}:${link.ends.map((e) => e.interface).join("-")}`} link={link} rates={rates} />
      ))}
    </div>
  );
}

function TrafficLegend() {
  return (
    <div>
      <h2>Traffic</h2>
      <svg className="traffic-key" viewBox="0 0 200 40" role="img" aria-label="Thin and pale: idle; thick and deep: busy">
        {[0, 0.25, 0.5, 0.75, 1].map((l, i) => (
          <line key={l} className="traffic-line" x1={10 + i * 40} y1={8} x2={30 + i * 40} y2={8} style={{ strokeWidth: 1.6 + 6 * l, opacity: 0.35 + 0.65 * l }} />
        ))}
        {["0 %", "50 %", "100 %"].map((t, i) => (
          <text key={t} x={20 + i * 80} y={32} textAnchor="middle">{t}</text>
        ))}
      </svg>
      <p className="muted">
        Each line shows the busier direction of its busiest port, as a share of the port speed, from the switches'
        counters (every 5 s; refreshed here every 10 s). A dotted line has no data: a planned switch, or a port with
        no counters.
      </p>
      <p className="muted">Click a line for the rates at both ends.</p>
    </div>
  );
}

function Legend() {
  return (
    <div>
      <h2>Legend</h2>
      <ul className="legend">
        <li><span className="swatch ok" /> ok: LLDP sees the cabled peer</li>
        <li><span className="swatch bad" /> missing: no LLDP neighbour on a cabled port</li>
        <li><span className="swatch bad dashed" /> mismatch: LLDP sees a different peer</li>
        <li><span className="swatch warn" /> unexpected: LLDP neighbour with no NetBox cable</li>
        <li><span className="swatch grey" /> unverified: no live data for either end</li>
      </ul>
      <ul className="legend">
        <li><span className="dotkey polled" /> polled recently</li>
        <li><span className="dotkey silent" /> active, not polled</li>
        <li><span className="dotkey planned" /> planned (faded box, no light)</li>
      </ul>
      <p className="muted">Each pair of leaves stands on its own floor; a dashed floor is a gateway pair. Several cables between two devices are drawn as one line. Click a switch or a line for details.</p>
    </div>
  );
}

function LinkDetails({ link, rates }: { link: TopologyLink; rates: FabricTraffic | null }) {
  return (
    <div className="end-group">
      <h3>
        <span className={`pill link-${link.status}`}>{link.status}</span>{" "}
        <span className="muted">{link.source === "netbox" ? "cable in NetBox" : "LLDP only, no NetBox cable"}</span>
      </h3>
      {link.ends.map((end) => (
        <EndDetails key={`${end.device}:${end.interface}`} end={end} rate={rates?.[end.device]?.[end.interface]} />
      ))}
    </div>
  );
}

function EndDetails({ end, rate }: { end: LinkEnd; rate?: { in_bps?: number; out_bps?: number } }) {
  const oper = end.oper_up === null ? "unknown" : end.oper_up ? "up" : "down";
  return (
    <div className="end">
      <h3 className="mono">
        {end.device} {end.interface}
      </h3>
      <dl>
        <dt>Check</dt>
        <dd>{end.check}</dd>
        <dt>Oper state</dt>
        <dd>{oper}</dd>
        {rate && (
          <>
            <dt>Traffic</dt>
            <dd>
              in {formatBps(rate.in_bps)} · out {formatBps(rate.out_bps)}{" "}
              <a href={`#/switches/${encodeURIComponent(end.device)}`}>history →</a>
            </dd>
          </>
        )}
        <dt>LLDP sees</dt>
        <dd>
          {end.lldp.length
            ? end.lldp.map((n) => (
                <div key={`${n.system_name}:${n.port_id}`} className="mono">
                  {n.system_name} {n.port_id}
                </div>
              ))
            : end.check === "unverified"
              ? "not polled"
              : "nothing"}
        </dd>
      </dl>
    </div>
  );
}
