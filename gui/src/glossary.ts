// Plain-language explanations of fabric terms: shown when a newcomer
// points at a dotted word. Short, written for someone new to this fabric, not to networking.

export const GLOSSARY: Record<string, string> = {
  VNI: "VNI: the number that names a segment across the whole fabric. Two ports in the same VNI are in the same layer-2 network, whichever leaf they are on.",
  BD: "Bridge domain: the segment's local number on each switch. The platform proposes the next free one.",
  "M-LAG": "M-LAG: two leaves working as one, so a server cabled to both keeps running if one fails. On the map the two stand on one floor.",
  VTEP: "VTEP: the address a leaf uses to carry segments to the other leaves over the fabric.",
  VPN: "VPN instance: a routing table of its own. A routed segment gets one, named <segment>-<bridge domain>.",
  gateway: "Gateway: the address hosts send traffic to when it leaves their segment. Every leaf where the segment is used answers on the same address.",
  "Eth-Trunk": "Eth-Trunk: several ports bundled into one link (LACP). An M-LAG trunk has members on both leaves of a pair.",
  "peer-link": "Peer-link: the cables between the two leaves of an M-LAG pair. They carry nothing else.",
  planned: "Planned: the switch exists only on paper. Nothing connects to it, polls it or deploys to it until it is activated.",
  active: "Active: the switch is real and reachable at its management address; it is polled and can be deployed to.",
  deploy: "Deploy: sending the change to the switches in waves (never both switches of a pair at once), each checked afterwards. Nothing is sent before an approver approves the patch.",
  patch: "Patch: the exact commands a switch would receive to match its intent. You read it before approving.",
  drift: "Differs: the switch's running config does not match intent: a change saved but not deployed, or something changed by hand on the switch.",
  spine: "Spine: the switches every leaf connects to; they carry traffic between leaves and share routes (route reflectors).",
  leaf: "Leaf: the switches servers and firewalls are cabled to.",
  "border-leaf": "Border leaf: a leaf pair that only routes (firewalls, ISPs): it has every routed segment, and no segment on its ports.",
  symmetric: "Symmetric fabric: every leaf routes the segments attached to it.",
  centralized: "Centralized fabric: one pair of switches (the gateway pair) does all the routing; the other leaves only switch.",
};
