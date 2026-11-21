import { describe, expect, it } from "vitest";
import type { SoftwareImage } from "./api";
import { downloadLine, mapMark, patchesFor, planLines, size } from "./software";

const image = (name: string, kind: "system" | "patch", release: string): SoftwareImage => ({
  id: name.length, name, kind, release, size: 1, sha256: "a".repeat(64), sha256_typed: false, uploaded_by: "root",
  uploaded_at: "2026-10-23T10:00:00Z", present: true,
});

describe("switch software", () => {
  const r25 = image("CE6800_V300R025C00SPC500.cc", "system", "V300R025C00SPC500");
  const images = [r25, image("a_V300R025SPH120.PAT", "patch", "V300R025SPH120"), image("b_V300R024SPH050.PAT", "patch", "V300R024SPH050")];

  it("offers only the patches of the chosen release", () => {
    expect(patchesFor(images, r25).map((i) => i.release)).toEqual(["V300R025SPH120"]);
    expect(patchesFor(images, null)).toHaveLength(2);
  });

  it("reads a plan as the orchestrator writes it", () => {
    const lines = planLines("# runs V300R024C00SPC100\ndelete /unreserved flash:/old.cc   # 421.4 MB\n"
      + "sftp client-transfile get ... sourcefile new.cc destination flash:/new.cc   # 589.4 MB\n"
      + "check file-digest digest-algorithm sha256 ab flash:/new.cc\nstartup system-software flash:/new.cc\n"
      + "reboot (save the configuration: Y, reboot: Y)\n# free flash after the downloads: 1.0 MB (now 2.0 MB)");
    expect(lines.map((l) => l.kind)).toEqual(["note", "delete", "download", "check", "startup", "reboot", "note"]);
    expect(lines[1]).toEqual({ kind: "delete", text: "delete /unreserved flash:/old.cc", note: "421.4 MB" });
  });

  it("tells how far a download is", () => {
    const p = { device: "leaf1", file: "x.cc", size: 600e6, bytes: 0, started: 0, rate: null };
    expect(downloadLine(p).text).toBe("waiting for leaf1 to start fetching 600.0 MB");
    expect(downloadLine({ ...p, bytes: 300e6, rate: 2e6 })).toEqual({ share: 0.5, text: "300.0 MB of 600.0 MB · 2.0 MB/s · about 3 min left" });
  });

  it("shows sizes and map states", () => {
    expect([size(589355684), size(2_888_000_000), size(512)]).toEqual(["589.4 MB", "2.89 GB", "1 kB"]);
    expect([mapMark("rebooting"), mapMark("verified")]).toEqual(["deploying", "verified"]);
  });
});
