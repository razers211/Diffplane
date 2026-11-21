import { expect, test } from "vitest";
import { url } from "./api";

test("a route's parameters are encoded in place, a port's slashes kept", () => {
  expect(url("/fabrics/{site}/checks", { path: { site: "dc 2" } })).toBe("/fabrics/dc%202/checks");
  expect(url("/devices/{name}/port-settings/{port}", { path: { name: "leaf/1", port: "10GE1/0/1" } }))
    .toBe("/devices/leaf%2F1/port-settings/10GE1/0/1");
  expect(() => url("/diff-jobs/{job_id}", {})).toThrow("no value for job_id");
});

test("the query leaves out unset values", () => {
  expect(url("/topology", { query: { site: undefined } })).toBe("/topology");
  expect(url("/fabrics/{site}/reach", { path: { site: "lab" }, query: { from: "a b", to: "10.0.0.1", port: undefined, proto: null } }))
    .toBe("/fabrics/lab/reach?from=a%20b&to=10.0.0.1");
  expect(url("/events", { query: new URLSearchParams({ kind: "link down" }) })).toBe("/events?kind=link+down");
});
