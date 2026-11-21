import type { OspfAuth } from "./api";

// Link authentication: where a key change stands, in plain words. Each step is deployed before
// the next one is offered; the API checks the switches' own keys before it allows the next.

export type RollStep = { action: "add" | "send" | "retire"; label: string; text: string };

export function nextStep(a: OspfAuth): RollStep | null {
  if (!a.enabled) return null;
  if (a.stage === "added") {
    return { action: "send", label: "2 of 3: Start using the new key", text: "Deploy step 1 first. Switches then sign with the new key and still accept the old one." };
  }
  if (a.stage === "moved") {
    return { action: "retire", label: "3 of 3: Remove the old key", text: "Deploy step 2 first. Then the old key can go." };
  }
  return { action: "add", label: "Change the key", text: "Step 1 of 3: adds a new key next to the current one. Links stay up at every step." };
}
