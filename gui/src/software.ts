// Switch software: images in the store and upgrade plans, as the orchestrator gives them.
import type { DeployStep, DownloadProgress, SoftwareImage } from "./api";

/** V300R025SPH120 belongs to V300R025C00SPC500 (the orchestrator checks the same). */
export const patchFits = (patch: string, system: string) => system.startsWith(patch.split("SPH")[0]!);

/** The patches to offer with a chosen system image (all patches when none is chosen). */
export function patchesFor(images: SoftwareImage[], system: SoftwareImage | null): SoftwareImage[] {
  return images.filter((i) => i.kind === "patch" && (!system || patchFits(i.release, system.release)));
}

export function size(bytes: number): string {
  return bytes >= 1e9 ? `${(bytes / 1e9).toFixed(2)} GB` : bytes >= 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1e3))} kB`;
}

export type PlanKind = "note" | "delete" | "download" | "check" | "startup" | "reboot";
/** A plan's lines, each with what it does: notes (`# ...`) and the commands in order. */
export function planLines(plan: string | null): { kind: PlanKind; text: string; note?: string }[] {
  return (plan ?? "").split("\n").filter(Boolean).map((line) => {
    if (line.startsWith("#")) return { kind: "note" as const, text: line.replace(/^#\s*/, "") };
    const [text, note] = line.split("   # ") as [string, string | undefined];
    const kind: PlanKind = text === "system-view" || text === "return" ? "check" : text.startsWith("delete") ? "delete" : text.startsWith("sftp") ? "download"
      : text.startsWith("check") ? "check" : text.startsWith("startup") ? "startup" : "reboot";
    return { kind, text, note };
  });
}

/** A download's bar: its share, and a line like "312.0 of 589.4 MB · 2.1 MB/s · about 2 min left". */
export function downloadLine(p: DownloadProgress): { share: number; text: string } {
  const share = p.size ? Math.min(1, p.bytes / p.size) : 0;
  if (!p.bytes) return { share, text: `waiting for ${p.device} to start fetching ${size(p.size)}` };
  const parts = [`${size(p.bytes)} of ${size(p.size)}`];
  if (p.rate) {
    parts.push(`${size(p.rate)}/s`);
    const left = (p.size - p.bytes) / p.rate;
    if (left > 0) parts.push(left < 90 ? `about ${Math.max(1, Math.round(left))} s left` : `about ${Math.round(left / 60)} min left`);
  }
  return { share, text: parts.join(" · ") };
}

/** An upgrade step's state on the map (the deploy marks: busy while it changes or reboots). */
export function mapMark(status: string): DeployStep["status"] {
  return status === "upgrading" || status === "rebooting" ? "deploying" : (status as DeployStep["status"]);
}
