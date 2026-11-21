import { describe as group, expect, it } from "vitest";
import type { AuditEvent, DeployJob, DriftDevice } from "./api";
import { barText, intentChanges, isIntentWrite, pending } from "./changes";

const dev = (device: string, state: DriftDevice["state"], site = "example"): DriftDevice =>
  ({ device, site, state, checked_at: null, job_id: 1, job_kind: "diff", diff: state === "out_of_sync" ? "+x" : "", error: null });
const job = (id: number, status: DeployJob["status"], site: string | null = "example", finished_at: string | null = null): DeployJob =>
  ({ id, status, site, requested_by: "op", approved_by: null, approved_at: null, generators: null,
     created_at: "2026-10-11T10:00:00Z", finished_at, error: null, steps: [] });

group("pending changes", () => {
  it("finds the step the fabric is at", () => {
    expect(pending("example", [dev("leaf1", "in_sync")], []).step).toBeNull();
    expect(pending("example", [dev("leaf1", "out_of_sync")], []).step).toBe("review");
    expect(pending("example", [dev("leaf1", "out_of_sync")], [job(3, "preparing")]).step).toBe("review");
    expect(pending("example", [dev("leaf1", "out_of_sync")], [job(3, "diffed")]).step).toBe("approve");
    expect(pending("example", [dev("leaf1", "out_of_sync")], [job(3, "deploying")]).step).toBe("deploy");
  });

  it("counts only the selected fabric", () => {
    const p = pending("example", [dev("leaf1", "out_of_sync"), dev("east-leaf1", "out_of_sync", "east")], [job(4, "diffed", "east")]);
    expect(p.outOfSync.map((d) => d.device)).toEqual(["leaf1"]);
    expect(p.awaiting).toBeNull();
    expect(pending("", [dev("leaf1", "out_of_sync"), dev("east-leaf1", "out_of_sync", "east")], []).outOfSync).toHaveLength(2);
  });

  it("says what waits, most urgent first", () => {
    expect(barText(pending("example", [dev("a", "out_of_sync"), dev("b", "out_of_sync")], []))).toMatch(/^2 switches differ/);
    expect(barText(pending("example", [dev("a", "out_of_sync")], [job(9, "diffed")]))).toBe("Deploy #9 waits for approval.");
    expect(barText(pending("example", [dev("a", "in_sync")], []))).toBeNull();
  });

  it("puts a window waiting for an approver first and a scheduled one last", () => {
    const again = job(11, "reapproval");
    expect(pending("example", [], [again]).step).toBe("deploy");
    expect(barText(pending("example", [dev("a", "out_of_sync")], [again]))).toMatch(/^Deploy #11 waits for an approver/);
    const later = { ...job(12, "scheduled"), window_start: "2026-10-31T01:00:00Z", window_end: "2026-10-31T04:00:00Z" };
    const sooner = { ...job(13, "scheduled"), window_start: "2026-10-30T01:00:00Z", window_end: "2026-10-30T04:00:00Z" };
    const p = pending("example", [dev("a", "in_sync")], [sooner, later]);
    expect([p.step, p.scheduled.map((j) => j.id)]).toEqual([null, [13, 12]]);
    expect(barText(p)).toMatch(/^Deploy #13 is scheduled for /);
    expect(barText(pending("example", [dev("a", "out_of_sync")], [sooner]))).toMatch(/^1 switch differs .* Deploy #13 is scheduled for /);   // still named while switches differ
  });

  it("knows which requests change intent", () => {
    expect(isIntentWrite("POST", "/segments")).toBe(true);
    expect(isIntentWrite("PUT", "/segments/example/10500/routing")).toBe(true);
    expect(isIntentWrite("PUT", "/fabrics/example/microsegmentation/contracts")).toBe(true);
    expect(isIntentWrite("GET", "/fabrics/example/microsegmentation")).toBe(false);
    expect(isIntentWrite("PUT", "/fabrics/example/qos/ports")).toBe(true);
    expect(isIntentWrite("GET", "/fabrics/example/qos")).toBe(false);
    expect(isIntentWrite("PUT", "/fabrics/example/protection/mode")).toBe(true);
    expect(isIntentWrite("PUT", "/devices/leaf1/protection/10GE1/0/3")).toBe(true);
    expect(isIntentWrite("PUT", "/devices/leaf1/port-settings/10GE1/0/3")).toBe(true);
    expect(isIntentWrite("PUT", "/fabrics/example/port-settings/enabled")).toBe(true);
    expect(isIntentWrite("GET", "/fabrics/example/port-settings/adoption")).toBe(false);
    expect(isIntentWrite("PUT", "/routing/example/devices/leaf1+leaf2")).toBe(true);
    expect(isIntentWrite("POST", "/devices/leaf1/trunks")).toBe(true);
    expect(isIntentWrite("PUT", "/fabrics/example/ntp")).toBe(true);
    expect(isIntentWrite("PUT", "/fabrics/example/snmp")).toBe(true);
    expect(isIntentWrite("PUT", "/fabrics/example/snmp/readers/nms")).toBe(true);
    expect(isIntentWrite("DELETE", "/fabrics/example/snmp/traps/nms-1")).toBe(true);
    expect(isIntentWrite("PUT", "/fabrics/example/snmp/readers/nms/passwords")).toBe(false);   // not intent
    expect(isIntentWrite("PUT", "/fabrics/example/gateway-mac")).toBe(true);
    expect(isIntentWrite("PUT", "/devices/leaf1/role")).toBe(true);
    expect(isIntentWrite("POST", "/fabrics/example/intent-snapshots/3/rollback")).toBe(true);
    expect(isIntentWrite("GET", "/fabrics/example/intent-snapshots/3/rollback")).toBe(false);
    expect(isIntentWrite("PUT", "/fabrics/example/deploys")).toBe(false);
    expect(isIntentWrite("GET", "/segments?site=example")).toBe(false);
    expect(isIntentWrite("POST", "/deploy-jobs")).toBe(false);
    expect(isIntentWrite("POST", "/devices/leaf1/hostkeys/fetch")).toBe(false);
    expect(isIntentWrite("PUT", "/devices/leaf1/location")).toBe(false);
  });

  it("lists the fabric's intent changes since its last verified deploy", () => {
    const ev = (at: string, action: string, detail: string): AuditEvent => ({ at, user: "op", action, job_id: null, detail });
    const audit = [
      ev("2026-10-11T12:00:00Z", "segment.create", "example vni=10700"),
      ev("2026-10-11T11:30:00Z", "trunk.create", "leaf1 Eth-Trunk2"),
      ev("2026-10-11T11:20:00Z", "segment.create", "east vni=10800"),
      ev("2026-10-11T11:10:00Z", "deploy.approve", "example"),
      ev("2026-10-11T09:00:00Z", "segment.delete", "example vni=10500"),   // before the last deploy
    ];
    const deploys = [job(5, "verified", "example", "2026-10-11T10:00:00Z")];
    expect(intentChanges("example", audit, deploys, ["leaf1"]).map((e) => e.detail)).toEqual(["example vni=10700", "leaf1 Eth-Trunk2"]);
  });
});
