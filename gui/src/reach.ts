// Reachability: the choices of the page and what the map shows of an answer.
import type { Reach, ReachCheck } from "./api";

export type Chip = "Anything" | "Web" | "SSH" | "Ping" | "Expert";
export const CHIPS: { chip: Chip; hint: string }[] = [
  { chip: "Anything", hint: "Any traffic" }, { chip: "Web", hint: "TCP 443" }, { chip: "SSH", hint: "TCP 22" },
  { chip: "Ping", hint: "ICMP" }, { chip: "Expert", hint: "Choose the protocol and port" },
];

/** The query's protocol and port, or a sentence saying what to fix. */
export function traffic(chip: Chip, proto: string, port: string): { proto?: string; port?: number } | string {
  if (chip === "Web") return { proto: "tcp", port: 443 };
  if (chip === "SSH") return { proto: "tcp", port: 22 };
  if (chip === "Ping") return { proto: "icmp" };
  if (chip === "Anything" || proto === "any") return {};
  if (!port.trim()) return { proto };
  if (proto !== "tcp" && proto !== "udp") return "Only TCP and UDP have ports.";
  const number = Number(port);
  return Number.isInteger(number) && number >= 1 && number <= 65535 ? { proto, port: number } : "A port is a number from 1 to 65535.";
}

const LINKS: [string, string][] = [
  ["#/network/security", "Open Security groups"], ["#/network/segments", "Open Segments"],
  ["#/network/protection", "Open Port protection"], ["#/changes", "Open Changes"], ["#/switches/", "Open the switch"],
];

export function fixLabel(page: string): string {
  return LINKS.find(([prefix]) => page.startsWith(prefix))?.[1] ?? "Open the page";
}

/** The switches to draw: up to the stop on the way there, all of them otherwise. */
export function drawnPath(result: Reach): { path: string[]; stop?: string } {
  const stop = result.stop;
  if (!stop?.switch) return { path: result.switches };
  if (stop.direction === "back") return { path: result.switches, stop: stop.switch };
  const at = result.switches.indexOf(stop.switch);
  return { path: at < 0 ? [stop.switch] : result.switches.slice(0, at + 1), stop: stop.switch };
}

export const checkResult = (c: ReachCheck) => (c.ok === true ? "passed" : c.ok === false ? "failed" : "not known");
