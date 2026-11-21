import { describe, expect, it } from "vitest";
import { protocolName } from "./Flows";

describe("flow services", () => {
  it("names the protocol, and the port where it means a service", () => {
    expect(protocolName(6, 443)).toBe("TCP 443");
    expect(protocolName(17, 53)).toBe("UDP 53");
    expect(protocolName(1, 0)).toBe("ICMP");
    expect(protocolName(89, 0)).toBe("proto 89");
  });
});
