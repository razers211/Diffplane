// Where each segment's traffic leaves: a segment dropped on a firewall takes its default route
// there, dropped on another segment it peers with it. Pure, so it is unit tested; Exits.tsx renders it.

import type { Segment } from "./api";

/** The door a segment sits at: not routed, routed with no default route, or its firewall. */
export const doorOf = (s: Segment): string | null | undefined => (s.routing ? s.routing.firewall : undefined);

/** The routing request that keeps a routed segment's subnet and peers and changes one thing. */
export function routingBody(s: Segment, change: { firewall?: string | null; peers?: number[] }) {
  const length = Number(s.routing?.gateway.split("/")[1]);
  return {
    routed: true,
    prefix_length: length >= 8 && length <= 30 ? length : 24,
    firewall: "firewall" in change ? change.firewall ?? null : s.routing?.firewall ?? null,
    peers: change.peers ?? s.routing?.peers ?? [],
  };
}
