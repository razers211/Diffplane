import { describe, expect, it } from "vitest";
import type { DeployJob, Device, DriftDevice, Topology } from "./api";
import { deployTone, load, portSpeed, summarize } from "./overview";

const dev = (name: string, status = "active", site = "example"): Device => ({ name, site, role: "leaf", status, primary_ip: null, location: null, rack: null, position: null, face: null, pinned: false });
const drift = (device: string, state: DriftDevice["state"]): DriftDevice => ({
  device, site: "example", state, checked_at: null, job_id: null, job_kind: null, diff: null, error: state === "error" ? "timeout" : null,
});
const job = (id: number, status: DeployJob["status"], site: string | null = "example"): DeployJob => ({
  id, status, site, requested_by: "ops", approved_by: null, approved_at: null, generators: null,
  created_at: "", finished_at: null, error: null,
  steps: [{ device: "leaf1", status: status === "diffed" ? "diffed" : "verified", patch: null, error: null, started_at: null, finished_at: null }],
});
const topo = (collected: string[], links: Topology["links"] = []): Topology => ({
  nodes: collected.map((name) => ({ name, site: "example", role: "leaf", status: "active", in_netbox: true, collected: true })),
  links, telemetry: { ok: true, error: null },
});

describe("port speed and load", () => {
  it("reads the speed from the port name", () => {
    expect(portSpeed("10GE1/0/3")).toBe(1e10);
    expect(portSpeed("100GE1/0/1")).toBe(1e11);
    expect(portSpeed("GE1/0/1")).toBe(1e9);
    expect(portSpeed("MEth0/0/0")).toBe(1e9);
    expect(portSpeed("Eth-Trunk1")).toBeNull();
  });
  it("uses the busier direction", () => {
    expect(load({ interface: "10GE1/0/3", in_bps: 2.5e9, out_bps: 5e9 })).toBe(50);
    expect(load({ interface: "Eth-Trunk1", in_bps: 1, out_bps: 1 })).toBeNull();
  });
});

describe("summarize", () => {
  const base = {
    site: "example",
    devices: [dev("leaf1"), dev("leaf2", "planned"), dev("east-leaf1", "active", "east")],
    drift: [drift("leaf1", "in_sync"), drift("east-leaf1", "out_of_sync")],
    topology: topo(["leaf1"]),
    deploys: [job(7, "verified"), job(8, "diffed"), job(9, "failed", "east")],
    overview: { alerts: [], ports: [], telemetry: { ok: true, error: null } },
  };

  it("counts only the chosen fabric", () => {
    const { tiles, attention, history } = summarize(base);
    const by = Object.fromEntries(tiles.map((t) => [t.key, t]));
    expect(by.switches.value).toBe(2);
    expect(by.switches.sub).toBe("1 active · 1 planned");
    expect([by.sync.value, by.sync.of, by.sync.tone]).toEqual([1, 1, "good"]);
    expect([by.collector.value, by.collector.of, by.collector.tone]).toEqual([1, 1, "good"]);
    expect(by.approvals.value).toBe(1);
    expect(attention.map((a) => a.title)).toEqual(["Approve deploy #8"]);
    expect(history.map((j) => j.id)).toEqual([7, 8]);       // east's deploy is not shown
  });

  it("lists control-plane problems with a link to the switch's tab", () => {
    const { attention } = summarize({ ...base, overview: { ...base.overview,
      control: [{ device: "leaf1", problem: "BGP peer 10.10.0.1 (spine1), evpn: active" }] } });
    expect(attention.map((a) => [a.title, a.detail, a.href])).toEqual([
      ["Approve deploy #8", expect.any(String), "#/changes/deploys/8"],
      ["leaf1: control plane", "BGP peer 10.10.0.1 (spine1), evpn: active", "#/switches/leaf1/control"],
    ]);
  });

  it("puts approvals first, then failures, then warnings", () => {
    const { attention, tiles } = summarize({
      ...base,
      drift: [drift("leaf1", "out_of_sync")],
      topology: topo([], [{ source: "netbox", status: "missing", ends: [
        { device: "leaf1", interface: "100GE1/0/1", oper_up: false, lldp: [], check: "missing" },
        { device: "spine1", interface: "100GE1/0/1", oper_up: null, lldp: [], check: "unverified" }] }]),
      overview: { alerts: [{ alert: "FabricDriftStopped", device: null, severity: null, since: null }], ports: [], telemetry: { ok: true, error: null } },
    });
    expect(attention.map((a) => a.tone)).toEqual(["info", "bad", "bad", "warn", "warn"]);
    expect(attention[1].href).toBe("#/changes");
    expect(tiles.find((t) => t.key === "sync")?.tone).toBe("warn");
    expect(tiles.find((t) => t.key === "alerts")?.value).toBe(1);
  });

  it("says when telemetry is unavailable instead of blaming the switches", () => {
    const { tiles, attention } = summarize({ ...base, topology: null });
    expect(tiles.find((t) => t.key === "collector")?.sub).toBe("telemetry unavailable");
    expect(attention.some((a) => a.title.includes("not polled"))).toBe(false);
  });
});

describe("deployTone", () => {
  it("maps states", () => {
    expect(deployTone("verified")).toBe("good");
    expect(deployTone("failed")).toBe("bad");
    expect(deployTone("diffed")).toBe("info");
  });
});
