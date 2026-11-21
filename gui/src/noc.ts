// The NOC dashboard (Overview › NOC, wall mode at #/overview/noc/wall).
// Pure, so it is unit tested; Noc.tsx only renders it.

import type { FabricTraffic, Flows, Topology } from "./api";

export const ROTATE_S = 15;           // wall mode: seconds per fabric
export const TRAFFIC_WINDOW_S = 120;  // the traffic chart's span

const PHYSICAL = /^\d*GE\d+\/\d+\/\d+$/;

/** Traffic entering the fabric now: every physical front-panel port's in bits/s, except fabric links
 *  between switches (each such packet is counted again on the next switch) and MEth. */
export function edgeIn(traffic: FabricTraffic, topology: Topology | null): number {
  const fabric = new Set((topology?.links ?? []).flatMap((l) => l.ends.map((e) => `${e.device} ${e.interface}`)));
  let sum = 0;
  for (const [device, ports] of Object.entries(traffic)) {
    for (const [port, rate] of Object.entries(ports)) {
      if (PHYSICAL.test(port) && !fabric.has(`${device} ${port}`)) sum += rate.in_bps ?? 0;
    }
  }
  return sum;
}

export interface Sample { t: number; bps: number }

/** The samples kept for the chart: the new one added, those older than the window dropped. */
export function keep(samples: Sample[], next: Sample): Sample[] {
  return [...samples, next].filter((s) => s.t > next.t - TRAFFIC_WINDOW_S * 1000);
}

/** The fabric after `current` in wall mode's rotation (the first when `current` is not in it). */
export function nextFabric(sites: string[], current: string): string {
  if (sites.length === 0) return current;
  return sites[(sites.indexOf(current) + 1) % sites.length];
}

/** Segment pairs by traffic, busiest first (NetStream, edge ports only). */
export function talkers(flows: Flows | null, top = 5): { from: string; to: string; bps: number }[] {
  return (flows?.segments ?? [])
    .map((s) => ({ from: s.src_segment || "outside", to: s.dst_segment || "outside", bps: s.bps }))
    .filter((s) => s.bps > 0)
    .sort((a, b) => b.bps - a.bps)
    .slice(0, top);
}

/** A round axis maximum above `v` (1, 2 or 5 times a power of ten). */
export function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

// Alert rules (deploy/prometheus/alerts.yml) in plain words for the wall screen.
const ALERTS: Record<string, string> = {
  FabricBgpPeerDown: "BGP peer down", FabricCollectorDeviceDown: "Switch not polled", FabricDeviceOutOfSync: "Out of sync",
  FabricDriftCheckStopped: "Drift check stopped", FabricFanMissing: "Fan missing", FabricInterfaceErrors: "Port errors rising",
  FabricLinkFlapping: "Link flapping", FabricMlagHeartbeatLost: "M-LAG heartbeat lost", FabricMlagPeerLinkDown: "M-LAG peer-link down",
  FabricOpticRxPower: "Optic receive power", FabricOpticTemperature: "Optic temperature", FabricOpticTxPower: "Optic transmit power",
  FabricOspfNeighbourDown: "OSPF neighbour down", FabricPowerModuleFailed: "Power module failed", FabricTemperatureHigh: "Temperature high",
  FabricTrafficAnomaly: "Unusual traffic", FabricTestAlert: "Test alert",
};
export const alertLabel = (name: string) => ALERTS[name] ?? name;

/** A short axis label: 10G, 500M. */
export function axisBps(v: number): string {
  if (v >= 1e9) return `${+(v / 1e9).toFixed(1)}G`;
  if (v >= 1e6) return `${+(v / 1e6).toFixed(1)}M`;
  return v ? `${+(v / 1e3).toFixed(1)}k` : "0";
}
