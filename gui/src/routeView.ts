import type { RibRoute } from "./api";

// The route view's table helpers. Pure, so they are unit tested.

export const PAGE = 100; // the whole table, 100 rows per page

export interface RouteFilter { vpn: string; protocol: string; text: string; inactive: boolean }

/** Routes of one instance (or all), one protocol (or all), matching the text in prefix, next hop or interface. */
export function filterRoutes(routes: RibRoute[], f: RouteFilter): RibRoute[] {
  const text = f.text.trim().toLowerCase();
  return routes.filter((r) =>
    (!f.vpn || r.vpn === f.vpn) && (!f.protocol || r.protocol === f.protocol) && (f.inactive || r.active)
    && (!text || [r.prefix, r.next_hop, r.interface].some((v) => (v ?? "").toLowerCase().includes(text))));
}

export const pageCount = (n: number) => Math.max(1, Math.ceil(n / PAGE));
export const page = <T,>(rows: T[], n: number) => rows.slice(n * PAGE, (n + 1) * PAGE);

/** Values present in the rows, sorted, for a filter's choices. */
export const choices = (routes: RibRoute[], key: "vpn" | "protocol") => [...new Set(routes.map((r) => r[key]))].sort();

/** VPN instances to offer on the BGP tab: the global table, then every instance but the management one. */
export const bgpInstances = (routes: RibRoute[]) =>
  [null, ...choices(routes, "vpn").filter((v) => v !== "_public_" && v !== "_management_vpn_")];
