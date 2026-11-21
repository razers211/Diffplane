import { useMemo, useState } from "react";
import { fetchFaceplate, fetchInterfaces, type OpticLevel } from "./api";
import { KIND_LABEL, layout, type FacePort, type PortKind } from "./frontPanel";
import { useFetch } from "./useFetch";
import { dropZone } from "./dragdrop";

// The drawn front panel: pick ports by clicking them. Colours say what a
// port carries; a port that cannot be picked shakes and says why. Positions are schematic.

const FILL: Record<PortKind, string> = {
  free: "transparent", segment: "var(--p-segment)", service: "var(--p-service)", trunk: "var(--p-trunk)",
  peer: "url(#fp-hatch)", fabric: "var(--p-fabric)",
};

export interface Pick {
  /** Ports (or trunk names) picked so far. */
  selected: string[];
  /** Why this port cannot be picked here, or null when it can. */
  refuse: (port: FacePort) => string | null;
  /** What picking this port picks: the port, or its trunk. */
  target?: (port: FacePort) => string;
  onToggle: (target: string, port: FacePort) => void;
}

// An optical module's mark in the cage's corner: its level, grey when the port is not in use.
const OPTIC_FILL: Record<OpticLevel, string> = { ok: "var(--good)", warning: "var(--warn)", alarm: "var(--bad)", unused: "var(--unused)" };
const OPTIC_TEXT: Record<OpticLevel, string> = { ok: "optic ok", warning: "optic warning", alarm: "optic alarm", unused: "optic, port not in use" };

// `drop` makes a port a drop zone (`zone`, taking `accept`; segments by default) for the target
// it names (or none), `paint` its fill.
export function Faceplate({ device, pick, refresh = 0, optics, drop, paint, zone = "port", accept = "segment", legend = true }: {
  device: string; pick?: Pick; refresh?: number; optics?: Record<string, OpticLevel>;
  drop?: (port: FacePort) => string | null; paint?: (port: FacePort) => string | undefined; zone?: string; accept?: string; legend?: boolean;
}) {
  const plate = useFetch(() => fetchFaceplate(device), [device, refresh]);
  const state = useFetch(() => fetchInterfaces(device).catch(() => null), [device]);
  const [shaking, setShaking] = useState<string | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  const up = useMemo(() => new Set((state.data?.interfaces ?? []).filter((i) => i.oper_up).map((i) => i.interface)), [state.data]);
  const drawn = useMemo(() => layout(plate.data?.ports ?? []), [plate.data]);

  if (plate.error) return <p className="error">{plate.error}</p>;
  if (!plate.data) return <div className="chassis loading">Drawing {device}…</div>;

  const click = (port: FacePort) => {
    if (!pick) return setSaid(`${port.name} · ${KIND_LABEL[port.kind]}${port.detail ? `: ${port.detail}` : ""}`);
    const reason = pick.refuse(port);
    if (reason) {
      setShaking(null);
      requestAnimationFrame(() => setShaking(port.name));
      return setSaid(`${port.name}: ${reason}`);
    }
    setSaid(null);
    pick.onToggle(pick.target ? pick.target(port) : port.name, port);
  };
  const isSelected = (port: FacePort) => !!pick && pick.selected.includes(pick.target ? pick.target(port) : port.name);

  return (
    <div className="faceplate">
      <div className="chassis">
        <div className="chassis-label"><span>{device}{plate.data.model ? ` · ${plate.data.model}` : ""}</span><span>front panel (schematic order)</span></div>
        <svg viewBox={`0 0 ${drawn.width} ${drawn.height}`} role="group" aria-label={`Front panel of ${device}`}>
          <defs>
            <pattern id="fp-hatch" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <rect width="2" height="4" style={{ fill: "var(--unused)" }} />
            </pattern>
          </defs>
          {drawn.placed.map(({ port, x, y, w, h, top, label }) => (
            <g
              key={port.name}
              {...(drop?.(port) ? dropZone(zone, accept, drop(port)!) : {})}
              className={`fp-port k-${port.kind}${port.off ? " off" : ""}${isSelected(port) ? " sel" : ""}${shaking === port.name ? " no" : ""}${pick ? "" : " view"}`}
              tabIndex={0}
              role="button"
              aria-pressed={pick ? isSelected(port) : undefined}
              aria-label={`${port.name}, ${KIND_LABEL[port.kind]}${port.detail ? `, ${port.detail}` : ""}`}
              onClick={() => click(port)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); click(port); } }}
            >
              <title>{`${port.name}${port.description ? ` “${port.description}”` : ""}${port.off ? " · turned off" : ""} · ${KIND_LABEL[port.kind]}${port.detail ? `: ${port.detail}` : ""}${optics?.[port.name] ? ` · ${OPTIC_TEXT[optics[port.name]!]}` : ""}`}</title>
              <rect className="cage" x={x} y={y} width={w} height={h} rx={2} />
              <rect className="fill" x={x + 3} y={y + 3} width={w - 6} height={h - 6} rx={1.5} style={{ fill: isSelected(port) ? undefined : paint?.(port) ?? FILL[port.kind] }} />
              {optics?.[port.name] && (
                <path className={`optic-mark o-${optics[port.name]}`} d={`M${x + w - 8} ${y + 1}h7v7z`} style={{ fill: OPTIC_FILL[optics[port.name]!] }} />
              )}
              <circle className={`led${up.has(port.name) ? " up" : ""}`} cx={x + w / 2} cy={top ? y - 4 : y + h + 4} r={1.8} />
              <text className="num" x={x + w / 2} y={top ? y - 9 : y + h + 13}>{label}</text>
            </g>
          ))}
        </svg>
      </div>
      {legend && <div className="fp-legend">
        {(["segment", "service", "trunk", "peer", "fabric", "free"] as PortKind[]).map((k) => (
          <span key={k}><i className={`k-${k}`} />{KIND_LABEL[k]}</span>
        ))}
        <span><i className="led-key" />link up</span>
      </div>}
      {said && <p className={`fp-said${pick ? " refuse" : ""}`} role="status">{said}</p>}
    </div>
  );
}
