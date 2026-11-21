import { describe, expect, it } from "vitest";
import type { Segment } from "./api";
import { relayPlaces } from "./Relay";

const seg = (name: string, vni: number, firewall: string | null, peers: number[] = []): Segment => ({
  fabric: "example", name, vni, bd_id: vni - 10000, attachments: [],
  routing: { gateway: "10.100.0.1/24", peers, firewall },
});

describe("where a DHCP server may sit", () => {
  const web = seg("web", 10500, "FW", [10600]);
  const db = seg("db", 10600, null, [10500]);

  it("offers the own VPN, the segment's firewall and its peered segments", () => {
    expect(relayPlaces(web, [web, db], false).map((p) => p.value)).toEqual(["", "service:FW", "segment:10600"]);
  });

  it("offers no peered segment in a centralized fabric, and no firewall when there is none", () => {
    expect(relayPlaces(web, [web, db], true).map((p) => p.value)).toEqual(["", "service:FW"]);
    expect(relayPlaces(db, [web, db], true).map((p) => p.value)).toEqual([""]);
  });
});
