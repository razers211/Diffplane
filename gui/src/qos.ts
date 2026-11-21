// QoS: one queue plan on every port of the fabric, DSCP marks per segment
// and per port. The plan itself is platform code (generators/qos.py); the page shows it.

export type QosMark = "storage" | "scavenger";

export interface QosView {
  enabled: boolean;
  segments: Record<string, QosMark>;                 // by L2 VNI
  ports: Record<string, Record<string, QosMark>>;    // switch -> port or Eth-Trunk -> mark
  plan: { strict: number[]; weights: Record<string, number>; marks: Record<QosMark, string> };
}

export const MARKS: { mark: QosMark; label: string; text: string }[] = [
  { mark: "storage", label: "Storage", text: "AF31: queue 3, the largest weighted share" },
  { mark: "scavenger", label: "Scavenger", text: "CS1: queue 1, what is left over" },
];

// Huawei's default DiffServ domain (a test switch): DSCP -> queue.
const QUEUES: { queue: number; cls: string; traffic: string }[] = [
  { queue: 7, cls: "cs7", traffic: "network control" },
  { queue: 6, cls: "cs6", traffic: "routing: OSPF, BGP, BFD" },
  { queue: 5, cls: "ef", traffic: "voice (EF)" },
  { queue: 4, cls: "af4", traffic: "video, interactive (AF4x)" },
  { queue: 3, cls: "af3", traffic: "storage (AF3x)" },
  { queue: 2, cls: "af2", traffic: "transactional (AF2x)" },
  { queue: 1, cls: "af1", traffic: "scavenger (CS1, AF1x)" },
  { queue: 0, cls: "be", traffic: "everything else" },
];

export interface PlanRow { queue: number; cls: string; traffic: string; mode: "PQ" | "DRR"; weight: number | null; share: number | null }

/** The plan as rows, highest queue first; a weighted queue's share is of what PQ leaves. */
export function planRows(plan: QosView["plan"]): PlanRow[] {
  const total = Object.values(plan.weights).reduce((a, b) => a + b, 0);
  return QUEUES.map((q) => {
    const weight = plan.weights[String(q.queue)] ?? null;
    const strict = plan.strict.includes(q.queue);
    return { ...q, mode: strict ? "PQ" : "DRR", weight: strict ? null : weight,
             share: strict || weight === null || !total ? null : Math.round((weight / total) * 100) };
  });
}

/** Port marks as rows sorted by switch and port. */
export function portRows(ports: QosView["ports"]): { device: string; port: string; mark: QosMark }[] {
  return Object.entries(ports).flatMap(([device, marks]) => Object.entries(marks).map(([port, mark]) => ({ device, port, mark })))
    .sort((a, b) => a.device.localeCompare(b.device) || a.port.localeCompare(b.port, "en", { numeric: true }));
}
