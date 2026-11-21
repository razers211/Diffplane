// Which leaves a form offers. In a symmetric fabric a border leaf only
// routes: its ports carry no segments, and once the fabric has border leaves new services go
// there. In a centralized fabric the role marks the gateway pair, whose ports carry both.
// The orchestrator refuses the same things; this only keeps the forms honest.

interface Placed { name: string; role: string | null }

const isLeaf = (d: Placed) => d.role === "leaf" || d.role === "border-leaf";

/** Leaves whose ports may carry segments. */
export function attachableLeaves<T extends Placed>(devices: T[], centralized: boolean): T[] {
  return devices.filter((d) => d.role === "leaf" || (centralized && d.role === "border-leaf"));
}

/** Leaves a new firewall or ISP service may sit on. */
export function serviceLeaves<T extends Placed>(devices: T[], centralized: boolean): T[] {
  const borders = devices.filter((d) => d.role === "border-leaf");
  return !centralized && borders.length > 0 ? borders : devices.filter(isLeaf);
}
