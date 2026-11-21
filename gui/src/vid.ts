// Parse the VLAN field of the attach form: empty means untagged. Pure, so it is unit tested.

export type VidResult = { ok: true; vid: number | null } | { ok: false; error: string };

export function parseVid(text: string): VidResult {
  const trimmed = text.trim();
  if (trimmed === "" || trimmed.toLowerCase() === "untagged") return { ok: true, vid: null };
  if (!/^\d+$/.test(trimmed)) return { ok: false, error: "VLAN ID must be a number, or empty for untagged" };
  const vid = Number(trimmed);
  if (vid < 1 || vid > 4094) return { ok: false, error: "VLAN ID must be 1-4094" };
  return { ok: true, vid };
}

export const vidLabel = (vid: number | null) => (vid === null ? "untagged" : `vid ${vid}`);
